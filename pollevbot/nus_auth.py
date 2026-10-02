"""Interactive NUS SSO for the Poll Everywhere participant site.

NUS uses Microsoft sign-in and MFA. A dedicated Chrome profile keeps that
session between runs; the bot copies only Poll Everywhere participant cookies
into its requests session after sign-in.
"""

import logging
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from requests import RequestException
from requests.cookies import create_cookie

from .endpoints import endpoints

logger = logging.getLogger(__name__)


class NusLoginError(RuntimeError):
    """The NUS browser session could not be connected to Poll Everywhere."""


def _sso_url(session, host):
    url = endpoints['firehose_auth'].format(host=host, timestamp=round(time.time() * 1000))
    response = session.get(url, timeout=15)
    response.raise_for_status()
    sso_url = response.json().get('saml_login_url')
    parsed = urlparse(sso_url or '')
    if (parsed.scheme != 'https' or parsed.hostname != 'id.polleverywhere.com'
            or parsed.path != '/auth/saml/nus_ad'):
        raise NusLoginError('The course host did not advertise the NUS SSO URL.')

    # Poll Everywhere returns the participant to this host after NUS SSO.
    query = parse_qs(parsed.query)
    query.update({'redirect': ['https://pollev.com/{}'.format(host)],
                  'token_required': ['false']})
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _copy_participant_cookies(context, session, cookies=None):
    for cookie in (context.cookies('https://pollev.com') if cookies is None else cookies):
        domain = cookie['domain'].lstrip('.')
        if domain != 'pollev.com':
            continue
        session.cookies.set_cookie(create_cookie(
            name=cookie['name'], value=cookie['value'],
            domain=cookie['domain'], path=cookie['path'],
            secure=cookie['secure'], expires=cookie['expires'] if cookie['expires'] > 0 else None))


def _identity_logged_in(context):
    # A signed-in Poll Everywhere browser leaves the identity login page.
    # An unauthenticated browser receives the login form with HTTP 200.
    response = context.request.get('https://id.polleverywhere.com/login',
                                   max_redirects=0, timeout=15000)
    destination = urlparse(response.headers.get('location', ''))
    return (response.status in (301, 302, 303, 307, 308)
            and destination.hostname in ('www.polleverywhere.com',
                                         'app.polleverywhere.com'))


def _participant_session_present(session):
    return any(cookie.name == 'polleverywhere_session_id'
               and cookie.domain.lstrip('.') == 'pollev.com'
               and not cookie.is_expired() for cookie in session.cookies)


def _session_state(context, session):
    _copy_participant_cookies(context, session)
    return _identity_logged_in(context), _participant_session_present(session)


def login(session, host, profile_dir=None, timeout=300, exchange_token=None):
    """Sign in through NUS and connect the browser session to ``session``.

    ``exchange_token`` receives a Poll Everywhere participant auth token if
    the SSO callback provides one. No NUS password or MFA code is handled here.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise NusLoginError(
            'NUS login needs Playwright. Install requirements-nus.txt locally.') from exc

    sso_url = _sso_url(session, host)
    profile = Path(profile_dir or Path(__file__).resolve().parent.parent / '.pollev-auth')
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    profile.chmod(0o700)
    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile), channel='chrome', headless=True)
        except Exception as exc:
            raise NusLoginError('Could not open Chrome for NUS SSO: {}'.format(exc)) from exc
        try:
            if all(_session_state(context, session)):
                logger.info('Reused the NUS browser identity session.')
                return
        finally:
            context.close()


        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile), channel='chrome', headless=False)
        except Exception as exc:
            raise NusLoginError('Could not open Chrome for NUS SSO: {}'.format(exc)) from exc
        try:
            page = context.pages[0] if context.pages else context.new_page()
            auth_token = [None]

            def capture_token(frame):
                if frame != page.main_frame:
                    return
                parsed = urlparse(frame.url)
                if parsed.hostname not in ('pollev.com', 'www.polleverywhere.com',
                                           'id.polleverywhere.com'):
                    return
                values = parse_qs(parsed.query).get('pe_auth_token')
                if values:
                    auth_token[0] = values[0]

            page.on('framenavigated', capture_token)
            logger.info('Complete NUS sign-in and MFA in the Chrome window.')
            page.goto(sso_url, wait_until='domcontentloaded', timeout=30000)
            deadline = time.monotonic() + timeout
            attempted_token = None
            prompted_for_name = False
            while time.monotonic() < deadline:
                if auth_token[0] and auth_token[0] != attempted_token:
                    attempted_token = auth_token[0]
                    if exchange_token:
                        try:
                            exchange_token(attempted_token)
                        except RequestException as exc:
                            logger.warning('Could not exchange participant token: %s',
                                           type(exc).__name__)
                try:
                    identity_ready, participant_ready = _session_state(context, session)
                    if identity_ready and participant_ready:
                        logger.info('NUS SSO participant session established.')
                        return
                    if identity_ready and not participant_ready and not prompted_for_name:
                        logger.info('Finish the respondent name prompt in Chrome.')
                        prompted_for_name = True
                except Exception as exc:
                    logger.warning('Could not check browser session: %s', type(exc).__name__)
                page.wait_for_timeout(2000)
            raise NusLoginError(
                'NUS sign-in did not establish a Poll Everywhere participant session '
                'before the login timeout.')
        finally:
            context.close()


def assist_host_check_in(session, host, verify, profile_dir=None,
                         timeout=300):
    """Let the participant complete a host check-in in real Chrome.

    `verify` returns the feed token and any pending activity ID after the user
    confirms the browser step, or None while the host still denies the feed.
    No location is supplied or emulated by the bot.
    """
    with NusHostBrowser(session, host, profile_dir) as browser:
        return browser.assist_check_in(verify, timeout)


class NusHostBrowser:
    """Keep the real course page visible and share its participant cookies."""

    def __init__(self, session, host, profile_dir=None):
        self.session = session
        self.host = host
        self.profile_dir = profile_dir
        self.playwright = None
        self.context = None
        self._last_cookies = None

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise NusLoginError('Host check-in needs Playwright.') from exc
        profile = Path(self.profile_dir or
                       Path(__file__).resolve().parent.parent / '.pollev-auth')
        profile.mkdir(mode=0o700, parents=True, exist_ok=True)
        profile.chmod(0o700)
        self.playwright = sync_playwright().start()
        try:
            self.context = self.playwright.chromium.launch_persistent_context(
                str(profile), channel='chrome', headless=False)
            page = self.context.pages[0] if self.context.pages else self.context.new_page()
            page.goto(endpoints['home'].format(host=self.host),
                      wait_until='domcontentloaded', timeout=30000)
            self.sync_cookies()
        except Exception as exc:
            self.__exit__(None, None, None)
            raise NusLoginError('Could not open Chrome at the course page: {}'.format(exc)) from exc
        logger.info('Course page is open in Chrome; complete any real-location '
                    'check-in there when requested.')
        return self

    def __exit__(self, *_):
        try:
            if self.context is not None:
                try:
                    self.context.close()
                except Exception as exc:
                    logger.debug('Chrome context already closed: %s', type(exc).__name__)
        finally:
            self.context = None
            if self.playwright is not None:
                try:
                    self.playwright.stop()
                except Exception as exc:
                    logger.debug('Chrome driver already stopped: %s', type(exc).__name__)
                finally:
                    self.playwright = None

    def sync_cookies(self):
        if self.context is None:
            raise NusLoginError('The course browser is not open.')
        try:
            cookies = self.context.cookies('https://pollev.com')
            signature = tuple(sorted((cookie['domain'], cookie['path'],
                                      cookie['name'], cookie['value'])
                                     for cookie in cookies))
            if signature != self._last_cookies:
                _copy_participant_cookies(self.context, self.session, cookies)
                self._last_cookies = signature
        except Exception as exc:
            raise NusLoginError('Could not read the course browser session.') from exc

    def assist_check_in(self, verify, timeout=300):
        """Wait for manual check-in, then verify the feed before resuming."""
        if not sys.stdin.isatty():
            raise NusLoginError('Host check-in needs an interactive terminal.')
        logger.info('Complete any host check-in in Chrome. If asked, allow '
                    'Chrome to use your real location.')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                input('Press Enter after the page confirms check-in (Ctrl+C to stop): ')
            except EOFError as exc:
                raise NusLoginError('Host check-in needs terminal input.') from exc
            self.sync_cookies()
            result = verify()
            if result is not None:
                logger.info('Course activity feed is accessible after browser check-in.')
                return result
            logger.warning('The course activity feed still denies access. '
                           'Check the Chrome page and retry.')
        raise NusLoginError('Host check-in was not completed before the timeout.')
