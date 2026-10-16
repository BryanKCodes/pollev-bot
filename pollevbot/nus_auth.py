"""Interactive NUS SSO for the Poll Everywhere participant site.

NUS uses Microsoft sign-in and MFA. A Chrome profile keeps that
session between runs; the bot copies only Poll Everywhere participant cookies
into its requests session after sign-in.
"""

import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from requests import RequestException, Session
from requests.cookies import create_cookie

from .endpoints import endpoints

logger = logging.getLogger(__name__)


class NusLoginError(RuntimeError):
    """The NUS browser session could not be connected to Poll Everywhere."""


class NusResponseNotReady(NusLoginError):
    """The response form failed before any submission was attempted."""


class NusResponseRateLimited(NusLoginError):
    """The server explicitly rejected a response with HTTP 429."""

    def __init__(self, retry_after=None):
        super().__init__('Poll Everywhere rate limited the response.')
        self.retry_after = retry_after


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
    # The participant frontend loads /api/profile and considers user.id > 0
    # signed in. A waiting page or an anonymous participant cookie is insufficient.
    # context.request shares Chrome's cookies. A Set-Cookie from a probe must
    # never overwrite the browser session that holds an in-progress OAuth flow.
    with Session() as probe:
        _copy_participant_cookies(context, probe)
        with probe.get(endpoints['participant_profile'], allow_redirects=False,
                       timeout=15) as response:
            if not 200 <= response.status_code < 300:
                raise NusLoginError('Participant account check returned HTTP {}.'.format(
                    response.status_code))
            data = response.json()
            user = data.get('user') if isinstance(data, dict) else None
            user_id = user.get('id') if isinstance(user, dict) else None
            if isinstance(user_id, str) and user_id.isdecimal():
                user_id = int(user_id)
            return isinstance(user_id, int) and not isinstance(user_id, bool) and user_id > 0


def _participant_session_present(session):
    return any(cookie.name == 'polleverywhere_session_id'
               and cookie.domain.lstrip('.') == 'pollev.com'
               and not cookie.is_expired() for cookie in session.cookies)


def _session_state(context, session):
    identity_ready = _identity_logged_in(context)
    _copy_participant_cookies(context, session)
    return identity_ready, _participant_session_present(session)


def _copy_exchanged_cookies(context, session):
    """Keep the browser in sync after the HTTP client exchanges an SSO token."""
    cookies = []
    for cookie in session.cookies:
        if cookie.domain.lstrip('.') != 'pollev.com' or cookie.is_expired():
            continue
        item = dict(name=cookie.name, value=cookie.value, domain=cookie.domain,
                    path=cookie.path, secure=cookie.secure,
                    httpOnly=cookie.has_nonstandard_attr('HttpOnly'))
        if cookie.expires is not None:
            item['expires'] = cookie.expires
        cookies.append(item)
    if cookies:
        context.add_cookies(cookies)


def _close_login_context(context):
    try:
        context.close()
    except Exception as exc:
        logger.debug('Sign-in browser already closed: %s', type(exc).__name__)


def _login_page(context, previous=None):
    pages = [page for page in context.pages if not page.is_closed()]
    if not pages:
        raise NusLoginError('Sign-in window closed before login was verified. '
                            'Click Start again and leave Chrome open until sign-in finishes.')
    return previous if previous in pages else pages[-1]


def _authentication_error_page(context):
    message = re.compile('We encountered a problem while trying to authenticate you',
                         re.IGNORECASE)
    for page in context.pages:
        if page.is_closed() or urlparse(page.url).hostname not in (
                'pollev.com', 'www.polleverywhere.com', 'id.polleverywhere.com'):
            continue
        error = page.get_by_text(message)
        if error.count() and error.first.is_visible():
            return page
    return None


def _open_course_login(page, host, cancel_event):
    """Use the participant site's own login link and its return/token parameters."""
    page.goto(endpoints['home'].format(host=host),
              wait_until='domcontentloaded', timeout=30000)
    page.bring_to_front()
    deadline = time.monotonic() + 5
    name = re.compile(r'^(?:log|sign)\s*in\b', re.IGNORECASE)
    while time.monotonic() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            raise NusLoginError('Sign-in cancelled.')
        for frame in page.frames:
            selectors = (frame.get_by_role('link', name=name),
                         frame.get_by_role('button', name=name),
                         frame.get_by_text(name))
            for candidates in selectors:
                for index in range(candidates.count()):
                    candidate = candidates.nth(index)
                    if candidate.is_visible():
                        candidate.click(timeout=15000)
                        return
        # The login link may appear only after the participant app initializes.
        page.wait_for_timeout(250)
    logger.info('Use the course page\'s Log in button to sign in with NUS.')


def login(session, host, profile_dir=None, timeout=300, exchange_token=None,
          force_interactive=False, cancel_event=None, notify_action=None):
    """Sign in through NUS and connect the browser session to ``session``.

    ``exchange_token`` receives a Poll Everywhere participant auth token if
    the SSO callback provides one. No NUS password or MFA code is handled here.
    """
    try:
        from playwright.sync_api import Error as PlaywrightError, sync_playwright
    except ImportError as exc:
        raise NusLoginError(
            'NUS login needs Playwright. Install requirements-nus.txt locally.') from exc

    profile = Path(profile_dir or Path(__file__).resolve().parent.parent / '.pollev-auth')
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    profile.chmod(0o700)
    with sync_playwright() as playwright:
        if cancel_event is not None and cancel_event.is_set():
            raise NusLoginError('Sign-in cancelled.')
        if not force_interactive:
            try:
                context = playwright.chromium.launch_persistent_context(
                    str(profile), channel='chrome', headless=True)
            except Exception as exc:
                raise NusLoginError('Could not open Chrome for NUS SSO: {}'.format(exc)) from exc
            try:
                if all(_session_state(context, session)):
                    logger.info('Reused the NUS browser identity session.')
                    return
            except Exception as exc:
                logger.warning('Saved login could not be verified (%s); sign in again.',
                               type(exc).__name__)
            finally:
                _close_login_context(context)

        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile), channel='chrome', headless=False)
        except Exception as exc:
            raise NusLoginError('Could not open Chrome for NUS SSO: {}'.format(exc)) from exc
        browser_closed = [False]
        try:
            page = context.pages[0] if context.pages else context.new_page()
            auth_token = [None]
            context.on('close', lambda *_: browser_closed.__setitem__(0, True))

            def watch_page(target):
                def capture_token(frame):
                    if frame != target.main_frame:
                        return
                    parsed = urlparse(frame.url)
                    if parsed.scheme != 'https' or parsed.hostname not in (
                            'pollev.com', 'www.polleverywhere.com', 'id.polleverywhere.com'):
                        return
                    for parameters in (parsed.query, parsed.fragment.split('?', 1)[-1]):
                        values = parse_qs(parameters).get('pe_auth_token')
                        if values:
                            auth_token[0] = values[0]
                target.on('framenavigated', capture_token)
                capture_token(target.main_frame)

            for target in context.pages:
                watch_page(target)
            context.on('page', watch_page)
            logger.info('Complete NUS sign-in and MFA in the Chrome window.')
            if notify_action:
                notify_action('Complete NUS sign-in and MFA in Chrome, then finish any respondent-name prompt.')
            try:
                _open_course_login(page, host, cancel_event)
            except PlaywrightError:
                if not page.is_closed():
                    raise
                page = _login_page(context)
            deadline = time.monotonic() + timeout
            attempted_token = None
            token_attempts = 0
            next_exchange = 0
            next_warning = 0
            prompted_for_name = False
            authentication_error_reported = False
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    raise NusLoginError('Sign-in cancelled.')
                if browser_closed[0]:
                    raise NusLoginError('Sign-in browser closed before login was verified. '
                                        'Click Start again and leave Chrome open until sign-in finishes.')
                page = _login_page(context, page)
                error_page = _authentication_error_page(context)
                if error_page is not None and not authentication_error_reported:
                    logger.warning('Poll Everywhere reported an authentication error. '
                                   'Click "please try again" in Chrome to restart sign-in.')
                    error_page.bring_to_front()
                    if notify_action:
                        notify_action('Poll Everywhere sign-in failed. Click "please try again" in Chrome.')
                    authentication_error_reported = True
                elif error_page is None:
                    authentication_error_reported = False
                if auth_token[0] != attempted_token:
                    attempted_token = auth_token[0]
                    token_attempts = 0
                    next_exchange = 0
                if (attempted_token and exchange_token and token_attempts < 3
                        and time.monotonic() >= next_exchange):
                    token_attempts += 1
                    next_exchange = time.monotonic() + 2 ** token_attempts
                    try:
                        _copy_participant_cookies(context, session)
                        if exchange_token(attempted_token):
                            _copy_exchanged_cookies(context, session)
                            token_attempts = 3
                    except RequestException as exc:
                        logger.warning('Could not exchange participant token: %s',
                                       type(exc).__name__)
                try:
                    identity_ready, participant_ready = _session_state(context, session)
                    if identity_ready and participant_ready:
                        logger.info('NUS SSO participant session established.')
                        if notify_action:
                            notify_action('Signed in. Connecting to the course host…')
                        return
                    if identity_ready and not participant_ready and not prompted_for_name:
                        logger.info('Finish the respondent name prompt in Chrome.')
                        page.bring_to_front()
                        if notify_action:
                            notify_action('Finish the respondent-name prompt in Chrome to connect your account.')
                        prompted_for_name = True
                except Exception as exc:
                    if browser_closed[0]:
                        raise NusLoginError('Sign-in browser closed before login was verified.') from exc
                    if time.monotonic() >= next_warning:
                        logger.warning('Could not verify the participant account yet (%s).',
                                       type(exc).__name__)
                        next_warning = time.monotonic() + 30
                try:
                    page.wait_for_timeout(2000)
                except PlaywrightError:
                    # SSO can finish in a new tab and close the original one.
                    page = _login_page(context)
            raise NusLoginError(
                'Poll Everywhere did not confirm a signed-in participant before the login timeout. '
                'Use the course page\'s Log in flow and finish any respondent-name prompt.')
        except PlaywrightError as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise NusLoginError('Sign-in cancelled.') from exc
            if browser_closed[0] or not context.pages:
                raise NusLoginError('Sign-in window closed before login was verified. '
                                    'Click Start again and leave Chrome open until sign-in finishes.') from exc
            raise NusLoginError('Could not complete browser sign-in ({}).'.format(
                type(exc).__name__)) from exc
        finally:
            _close_login_context(context)


def assist_host_check_in(session, host, verify, profile_dir=None,
                         timeout=300, confirm=None, cancel_event=None):
    """Let the participant complete a host check-in in real Chrome.

    `verify` returns the feed token and any pending activity ID after the user
    confirms the browser step, or None while the host still denies the feed.
    No location is supplied or emulated by the bot.
    """
    with NusHostBrowser(session, host, profile_dir) as browser:
        return browser.assist_check_in(verify, timeout, confirm, cancel_event)


class NusHostBrowser:
    """Keep the real course page visible and share its participant cookies."""

    def __init__(self, session, host, profile_dir=None):
        self.session = session
        self.host = host
        self.profile_dir = profile_dir
        self.playwright = None
        self.context = None
        self.page = None
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
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.goto(endpoints['home'].format(host=self.host),
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
            self.page = None
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

    def _text_response_form(self):
        """Find the visible, unanswered text form on the participant page."""
        if self.page is None or self.page.is_closed():
            return None
        for frame in self.page.frames:
            if frame.is_detached():
                continue
            submits = frame.get_by_role('button', name=re.compile(
                r'^(?:submit|send)(?:\s+(?:your\s+)?(?:answer|response))?$', re.IGNORECASE))
            buttons = [submits.nth(i) for i in range(submits.count())
                       if submits.nth(i).is_visible()]
            if len(buttons) != 1:
                continue
            # Do not mistake login/name fields for a response. Prefer explicit
            # response placeholders, then textareas or answer-labelled inputs.
            selectors = (
                frame.get_by_placeholder(re.compile(
                    r'type here|(?:enter|type) (?:(?:your|an?|the) )?(?:answer|response)|your response',
                    re.IGNORECASE)),
                frame.locator('textarea:not([readonly]):not([disabled])'),
                frame.get_by_role('textbox', name=re.compile(r'answer|response', re.IGNORECASE)),
            )
            field = None
            for selector in selectors:
                visible = [selector.nth(i) for i in range(selector.count())
                           if selector.nth(i).is_visible() and selector.nth(i).is_enabled()
                           and selector.nth(i).get_attribute('readonly') is None]
                if len(visible) == 1:
                    field = visible[0]
                    break
                if len(visible) > 1:
                    break
            if field is None:
                continue
            body = frame.locator('body').inner_text(timeout=2000)
            if re.search(r'^\s*you (?:have (?:already )?)?responded\b', body,
                         re.IGNORECASE | re.MULTILINE):
                continue
            if re.search(r'activity title (?:is )?hidden', body, re.IGNORECASE):
                question = ''
            else:
                headings = frame.locator('h1, h2').all_inner_texts()
                question = next((text.strip() for text in headings if text.strip()), '')
            return field, buttons[0], question
        return None

    def visible_text_question(self):
        """Return visible question text, empty for a hidden title, or None."""
        try:
            form = self._text_response_form()
        except Exception as exc:
            raise NusLoginError('Could not inspect the course response form.') from exc
        return None if form is None else form[2]

    def wait_for_text_question(self, timeout=8, cancel_event=None):
        """Allow the participant app to render a response field after navigation."""
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    return None
                question = self.visible_text_question()
                if question is not None:
                    return question
                if self.page is None or self.page.is_closed():
                    return None
                self.page.wait_for_timeout(250)
            return None
        except Exception as exc:
            raise NusResponseNotReady('The response form is still loading or unavailable.') from exc

    def already_responded(self):
        """Recognise an explicit participant confirmation without submitting."""
        if self.page is None or self.page.is_closed():
            return False
        try:
            for frame in self.page.frames:
                if frame.is_detached() or frame.locator('body').count() == 0:
                    continue
                body = frame.locator('body').inner_text(timeout=2000)
                if re.search(r'^\s*you (?:have (?:already )?)?responded\b', body,
                             re.IGNORECASE | re.MULTILINE):
                    return True
        except Exception as exc:
            raise NusLoginError('Could not inspect the participant response status.') from exc
        return False

    def submit_visible_text(self, text, expected_question, before_submit=None):
        """Submit through the actual participant form and observe its response."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        try:
            form = self._text_response_form()
            if form is None or form[2] != expected_question:
                raise NusResponseNotReady('The visible text activity changed before submission.')
            field, submit, _ = form
            field.fill(text, timeout=5000)
            deadline = time.monotonic() + 5
            while not submit.is_enabled() and time.monotonic() < deadline:
                self.page.wait_for_timeout(100)
            if not submit.is_enabled():
                raise NusResponseNotReady('The text activity submit button is disabled.')
        except NusResponseNotReady:
            raise
        except Exception as exc:
            raise NusResponseNotReady('Could not prepare the visible text response.') from exc

        if before_submit is not None:
            before_submit()
        try:
            def is_submission(response):
                parsed = urlparse(response.url)
                host = parsed.hostname or ''
                return ((host == 'pollev.com' or host.endswith('.polleverywhere.com'))
                        and response.request.method == 'POST'
                        and ('/results' in parsed.path or '/responses' in parsed.path))

            try:
                with self.page.expect_response(is_submission, timeout=10000) as event:
                    submit.click(timeout=5000)
                response = event.value
            except PlaywrightTimeoutError as exc:
                raise NusLoginError('Could not confirm the browser submission.') from exc
            if response.status == 429:
                raise NusResponseRateLimited(response.headers.get('retry-after'))
            if not response.ok:
                raise NusLoginError('Browser submission returned HTTP {}.'.format(
                    response.status))
            try:
                payload = response.json()
            except Exception:
                payload = None
            if isinstance(payload, dict) and (payload.get('error') or
                                               payload.get('errors') or
                                               payload.get('success') is False):
                raise NusLoginError('Browser submission was rejected.')
            return {'browser_submission': True}
        except NusLoginError:
            raise
        except Exception as exc:
            raise NusLoginError('Could not submit the visible text activity.') from exc

    def assist_check_in(self, verify, timeout=300, confirm=None, cancel_event=None):
        """Wait for manual check-in, then verify the feed before resuming."""
        if confirm is None and not sys.stdin.isatty():
            raise NusLoginError('Host check-in needs an interactive terminal.')
        logger.info('Complete any host check-in in Chrome. If asked, allow '
                    'Chrome to use your real location.')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise NusLoginError('Check-in cancelled.')
            # Show the current host before waiting, even if Chrome stayed open.
            try:
                if self.page is None or self.page.is_closed():
                    self.page = self.context.new_page()
                self.page.goto(endpoints['home'].format(host=self.host),
                               wait_until='domcontentloaded', timeout=30000)
                self.page.bring_to_front()
            except Exception as exc:
                raise NusLoginError('Could not show the course check-in page in Chrome.') from exc
            try:
                if confirm is not None:
                    if not confirm(deadline):
                        raise NusLoginError('Check-in cancelled or timed out.')
                else:
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
