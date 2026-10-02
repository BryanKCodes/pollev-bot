"""Poll Everywhere authentication, activity polling, and answer dispatch."""

import json
import logging
import shutil
import sys
import time
from typing import Optional

import requests

from .activity import ActivityKind
from .activity_normalization import MULTIPLE_CHOICE_ENDPOINT, normalize_activity
from .answer_providers import LegacyRandomProvider, UnsupportedAnswerKind
from .answer_validation import (InvalidAnswer, candidate_options,
                                clean_open_ended_answer, resolve_option_id)
from .answerer import AnswerProvider, TextAnswer
from .endpoints import endpoints
from .nus_auth import NusLoginError
from .open_ended_transport import OpenEndedTransport

logger = logging.getLogger(__name__)
__all__ = ['PollBot']
_STATUS_INTERVAL = 60


def _preview(value: str, limit: int = 160) -> str:
    """Keep one activity or answer readable on a single terminal line."""
    text = ' '.join(value.split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


class LoginError(RuntimeError):
    """Login failed."""


class PollSkipped(RuntimeError):
    """The activity cannot safely be answered with the available workflow."""


class RetryablePollError(RuntimeError):
    """A failure occurred before submission, or submission was rate limited."""


class SubmissionUncertain(RuntimeError):
    """A submission may have arrived; never submit this activity again."""


class PollBot:
    """Poll a host and answer supported activities with an answer provider.

    Random multiple-choice behavior remains the default. Set answer_mode to
    llm for the optional local provider, or inject an AnswerProvider. An
    open-ended transport requires separately verified Poll Everywhere routes.
    """

    def __init__(self, user: str, password: str, host: str,
                 login_type: str = 'uw', min_option: int = 0,
                 max_option: int = None, closed_wait: float = 5,
                 open_wait: float = 5, lifetime: float = float('inf'),
                 browser_profile: str = None, login_timeout: float = 300,
                 answer_mode: str = 'random',
                 answer_provider: Optional[AnswerProvider] = None,
                 open_ended_transport: Optional[OpenEndedTransport] = None,
                 request_timeout: float = 15, retry_limit: int = 3,
                 retry_backoff: float = 2,
                 max_open_chars: Optional[int] = None):
        login_type = login_type.lower()
        if login_type not in {'uw', 'pollev', 'nus'}:
            raise ValueError('Unsupported login_type: {!r}'.format(login_type))
        if answer_mode not in {'random', 'llm', 'skip'}:
            raise ValueError('answer_mode must be random, llm, or skip')
        if not isinstance(retry_limit, int) or isinstance(retry_limit, bool) or retry_limit < 1:
            raise ValueError('retry_limit must be a positive integer')
        if (request_timeout <= 0 or retry_backoff < 0 or closed_wait <= 0
                or open_wait < 0
                or (max_open_chars is not None and max_open_chars < 1)):
            raise ValueError('Invalid timeout, backoff, or answer length')
        candidate_options((), min_option, max_option)
        if login_type == 'pollev' and user.strip().lower().endswith('@uw.edu'):
            logger.warning("This looks like a UW email; login_type='uw' may be needed.")

        self.user = user
        self.password = password
        self.host = host
        self.login_type = login_type
        self.browser_profile = browser_profile
        self.login_timeout = login_timeout
        self.min_option = min_option
        self.max_option = max_option
        self.closed_wait = closed_wait
        self.open_wait = open_wait
        self.lifetime = lifetime
        self.start_time = time.time()
        self.request_timeout = request_timeout
        self.retry_limit = retry_limit
        self.retry_backoff = retry_backoff
        self.answer_mode = answer_mode
        self.open_ended_transport = open_ended_transport
        if answer_provider is not None and not isinstance(answer_provider, AnswerProvider):
            raise TypeError('answer_provider must implement AnswerProvider')
        if answer_provider is not None:
            self.answer_provider = answer_provider
        elif answer_mode == 'llm':
            from .llama_cpp_provider import LlamaCppProvider
            self.answer_provider = LlamaCppProvider.from_env()
        else:
            self.answer_provider = LegacyRandomProvider()
        self.max_open_chars = (max_open_chars if max_open_chars is not None else
                               getattr(getattr(self.answer_provider, 'config', None),
                                       'max_open_chars', 280))

        self.session = requests.Session()
        self.session.headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/70.0.3538.102 Safari/537.36'
        }
        self.answered_polls = set()
        self.skipped_polls = set()
        self.attempted_polls = set()
        self.in_flight_polls = set()
        self._failures = {}
        self._retry_after = {}
        self._next_firehose_warning_at = 0.0
        self._idle_status_visible = False

    def _show_idle_status(self) -> bool:
        """Refresh one terminal line after a check with no new activity."""
        if not sys.stderr.isatty():
            return False
        message = '{} Polling host {}; no new activity handled.'.format(
            time.strftime('%H:%M:%S'), self.host)
        width = shutil.get_terminal_size(fallback=(80, 24)).columns
        sys.stderr.write('\r\x1b[2K' + _preview(message, max(2, width - 1)))
        sys.stderr.flush()
        self._idle_status_visible = True
        return True

    def _clear_idle_status(self):
        if self._idle_status_visible:
            sys.stderr.write('\r\x1b[2K')
            sys.stderr.flush()
            self._idle_status_visible = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.session.close()

    @staticmethod
    def timestamp() -> float:
        return round(time.time() * 1000)

    def _get_csrf_token(self) -> str:
        url = endpoints['csrf'].format(timestamp=self.timestamp())
        try:
            response = self.session.get(url, timeout=self.request_timeout,
                                        allow_redirects=False)
            if not 200 <= response.status_code < 300:
                raise RetryablePollError('csrf_http_{}'.format(response.status_code))
            token = response.json().get('token')
            if not isinstance(token, str) or not token:
                raise RetryablePollError('csrf_token_missing')
            return token
        except (requests.RequestException, ValueError, AttributeError) as exc:
            raise RetryablePollError('csrf_fetch_failed') from exc

    def _exchange_auth_token(self, token: str) -> bool:
        try:
            csrf_token = self._get_csrf_token()
        except RetryablePollError:
            return False
        response = self.session.post(endpoints['participant_auth_token'],
                                     headers={'x-csrf-token': csrf_token},
                                     data={'token': token},
                                     timeout=self.request_timeout)
        return response.ok

    def _pollev_login(self) -> bool:
        logger.info('Logging into Poll Everywhere.')
        response = self.session.post(endpoints['login'],
                                     headers={'x-csrf-token': self._get_csrf_token()},
                                     data={'login': self.user, 'password': self.password},
                                     timeout=self.request_timeout)
        return response.ok and not response.text

    def _uw_login(self):
        import bs4 as bs
        import re
        from urllib.parse import urljoin

        logger.info('Logging into Poll Everywhere through MyUW.')
        response = self.session.get(endpoints['uw_saml'], timeout=self.request_timeout)
        soup = bs.BeautifulSoup(response.text, 'html.parser')
        login_form = soup.find('form', id='idplogindiv')
        if not login_form or not login_form.get('action'):
            return False
        login_url = urljoin(response.url, login_form['action'])
        response = self.session.post(login_url,
                                     data={'j_username': self.user,
                                           'j_password': self.password,
                                           '_eventId_proceed': 'Sign in'},
                                     timeout=self.request_timeout)
        soup = bs.BeautifulSoup(response.text, 'html.parser')
        saml_response = soup.find('input', attrs={'name': 'SAMLResponse'})
        if not saml_response:
            return False
        response = self.session.post(endpoints['uw_callback'],
                                     data={'SAMLResponse': saml_response['value']},
                                     timeout=self.request_timeout)
        auth_match = re.search(r'pe_auth_token=([^&]+)', response.url)
        return bool(auth_match and self._exchange_auth_token(auth_match.group(1)))

    def login(self):
        if self.login_type == 'nus':
            from .nus_auth import login as nus_login
            nus_login(self.session, self.host, profile_dir=self.browser_profile,
                      timeout=self.login_timeout,
                      exchange_token=self._exchange_auth_token)
            success = True
        elif self.login_type == 'uw':
            success = self._uw_login()
        else:
            success = self._pollev_login()
        if not success:
            raise LoginError('Poll Everywhere login failed.')
        logger.info('Poll Everywhere identity session established; checking host access.')

    def get_firehose_token(self) -> str:
        from uuid import uuid4
        self.session.cookies['pollev_visitor'] = str(uuid4())
        self.session.cookies['pollev_visit'] = str(uuid4())
        url = endpoints['firehose_auth'].format(host=self.host,
                                                timestamp=self.timestamp())
        response = self.session.get(url, timeout=self.request_timeout)
        if 'presenter not found' in response.text.lower():
            raise ValueError('The poll host was not found.')
        response.raise_for_status()
        registration = response.json()
        token = registration.get('firehose_token')
        if not isinstance(token, str) or not token.strip():
            if (registration.get('registration_suggested')
                    and registration.get('participant_self_registration') is False):
                raise LoginError(
                    'This host requires pre-registration and does not allow '
                    'self-registration. Sign in with the account on the course '
                    'roster or ask the presenter to add it.')
            raise LoginError(
                'This account is not authorized for the course activity feed. '
                'Check registration with the presenter and sign in with the registered account.')
        return token

    def get_new_poll_id(self, firehose_token=None) -> Optional[str]:
        if firehose_token:
            url = endpoints['firehose_with_token'].format(
                host=self.host, token=firehose_token, timestamp=self.timestamp())
        else:
            url = endpoints['firehose_no_token'].format(
                host=self.host, timestamp=self.timestamp())
        try:
            response = self.session.get(url, timeout=0.3)
            response.raise_for_status()
            message = json.loads(response.json()['message'])
            if not isinstance(message, dict):
                return None
            error = message.get('error')
            if isinstance(error, dict) and error.get('type') == 'UnauthorizedSubscription':
                raise LoginError('The course activity feed rejected this account.')
            if error:
                now = time.monotonic()
                if now >= self._next_firehose_warning_at:
                    self._clear_idle_status()
                    error_type = error.get('type') if isinstance(error, dict) else type(error).__name__
                    logger.warning('Activity feed returned an error (%s); retrying.',
                                   _preview(str(error_type), 60))
                    self._next_firehose_warning_at = now + _STATUS_INTERVAL
                return None
            poll_id = message.get('uid')
        except (requests.exceptions.ReadTimeout, KeyError):
            return None
        except (requests.RequestException, ValueError, TypeError) as exc:
            now = time.monotonic()
            if now >= self._next_firehose_warning_at:
                self._clear_idle_status()
                logger.warning('Activity feed read failed (%s); retrying.',
                               type(exc).__name__)
                self._next_firehose_warning_at = now + _STATUS_INTERVAL
            return None
        if not isinstance(poll_id, (str, int)) or isinstance(poll_id, bool):
            return None
        poll_id = str(poll_id)
        if (poll_id in self.answered_polls or poll_id in self.skipped_polls
                or poll_id in self.attempted_polls or poll_id in self.in_flight_polls
                or time.monotonic() < self._retry_after.get(poll_id, 0)):
            return None
        return poll_id

    def _fetch_activity(self, poll_id: str):
        url = endpoints['poll_data'].format(uid=poll_id)
        try:
            response = self.session.get(url, timeout=self.request_timeout,
                                        allow_redirects=False)
        except requests.RequestException as exc:
            raise RetryablePollError('activity_fetch_failed') from exc
        if response.status_code == 404 and self.open_ended_transport is not None:
            try:
                payload = self.open_ended_transport.fetch(
                    self.session, poll_id, self.request_timeout)
            except PollSkipped:
                raise
            except Exception as exc:
                raise RetryablePollError('open_ended_fetch_failed') from exc
            activity = normalize_activity(poll_id, payload)
            if activity.kind is not ActivityKind.OPEN_ENDED:
                raise PollSkipped('open_ended_route_type_unverified')
            return activity
        if response.status_code == 404:
            raise PollSkipped('no_verified_activity_route')
        if response.status_code in (401, 403, 408, 429) or response.status_code >= 500:
            raise RetryablePollError('activity_http_{}'.format(response.status_code))
        if not 200 <= response.status_code < 300:
            raise PollSkipped('activity_http_{}'.format(response.status_code))
        try:
            payload = response.json()
        except ValueError as exc:
            raise RetryablePollError('activity_invalid_json') from exc
        return normalize_activity(poll_id, payload,
                                  successful_endpoint=MULTIPLE_CHOICE_ENDPOINT)

    def _submit(self, poll_id: str, sender):
        if poll_id in self.attempted_polls:
            raise SubmissionUncertain('submission_already_attempted')
        token = self._get_csrf_token()
        self.attempted_polls.add(poll_id)
        try:
            response = sender(token)
        except Exception as exc:
            raise SubmissionUncertain('submission_transport_failed') from exc
        try:
            status_code = response.status_code
            accepted = 200 <= status_code < 300
        except AttributeError as exc:
            raise SubmissionUncertain('submission_invalid_response') from exc
        if status_code == 429:
            # A rate-limit rejection is explicit; another attempt can be made.
            self.attempted_polls.discard(poll_id)
            raise RetryablePollError('submission_rate_limited')
        if not accepted:
            raise SubmissionUncertain('submission_http_{}'.format(status_code))
        if status_code == 204:
            return {}
        try:
            result = response.json()
        except (ValueError, AttributeError) as exc:
            raise SubmissionUncertain('submission_invalid_json') from exc
        if (not isinstance(result, dict) or result.get('error')
                or result.get('errors') or result.get('success') is False):
            raise SubmissionUncertain('submission_rejected')
        return result

    def _submit_multiple_choice(self, poll_id: str, option_id):
        def send(token):
            return self.session.post(
                endpoints['respond_to_poll'].format(uid=poll_id),
                headers={'x-csrf-token': token},
                data={'option_id': option_id, 'isPending': True,
                      'source': 'pollev_page'},
                timeout=self.request_timeout, allow_redirects=False)
        return self._submit(poll_id, send)

    def _submit_open_ended(self, poll_id: str, text: str):
        if self.open_ended_transport is None:
            raise PollSkipped('open_ended_route_unverified')
        return self._submit(poll_id, lambda token: self.open_ended_transport.submit(
            self.session, poll_id, text, token, self.request_timeout))

    def answer_poll(self, poll_id) -> dict:
        """Fetch, classify, answer, and submit one activity exactly once."""
        poll_id = str(poll_id)
        if poll_id in self.answered_polls or poll_id in self.skipped_polls:
            raise PollSkipped('activity_already_handled')
        if poll_id in self.attempted_polls:
            raise SubmissionUncertain('submission_already_attempted')
        if poll_id in self.in_flight_polls:
            raise PollSkipped('activity_in_flight')
        self.in_flight_polls.add(poll_id)
        try:
            if self.answer_mode == 'skip':
                raise PollSkipped('answer_mode_skip')
            activity = self._fetch_activity(poll_id)
            logger.info('Activity %s: %s question: %s', poll_id,
                        activity.kind.value, _preview(activity.question) or '(unavailable)')
            if activity.kind is ActivityKind.UNSUPPORTED:
                raise PollSkipped('unsupported_activity')
            text_required = not isinstance(self.answer_provider, LegacyRandomProvider)
            if not activity.question and text_required:
                raise PollSkipped('missing_question')
            if activity.kind is ActivityKind.MULTIPLE_CHOICE:
                candidates = candidate_options(activity.options, self.min_option,
                                               self.max_option)
                if not candidates:
                    raise PollSkipped('no_candidate_options')
                if text_required and any(not option.text for option in candidates):
                    raise PollSkipped('missing_candidate_text')
                logger.info('Activity %s: selecting from %s options (%s mode).',
                            poll_id, len(candidates), self.answer_mode)
                try:
                    selection = self.answer_provider.select_option(
                        activity.question, [option.text for option in candidates])
                except UnsupportedAnswerKind as exc:
                    raise PollSkipped('provider_does_not_support_activity') from exc
                except Exception as exc:
                    logger.warning('Activity %s: answer generation failed (%s).',
                                   poll_id, type(exc).__name__)
                    raise RetryablePollError('answer_generation_failed') from exc
                try:
                    option_id = resolve_option_id(selection, candidates)
                except (InvalidAnswer, AttributeError) as exc:
                    raise RetryablePollError('invalid_provider_answer') from exc
                logger.info('Activity %s: selected option %s/%s: %s', poll_id,
                            selection.index + 1, len(candidates),
                            _preview(candidates[selection.index].text) or '(no text)')
                result = self._submit_multiple_choice(poll_id, option_id)
            elif activity.kind is ActivityKind.OPEN_ENDED:
                if self.open_ended_transport is None:
                    raise PollSkipped('open_ended_route_unverified')
                try:
                    response = self.answer_provider.answer_open_ended(activity.question)
                except UnsupportedAnswerKind as exc:
                    raise PollSkipped('provider_does_not_support_activity') from exc
                except Exception as exc:
                    logger.warning('Activity %s: answer generation failed (%s).',
                                   poll_id, type(exc).__name__)
                    raise RetryablePollError('answer_generation_failed') from exc
                if not isinstance(response, TextAnswer):
                    raise RetryablePollError('invalid_text_answer_type')
                try:
                    text = clean_open_ended_answer(response.text, self.max_open_chars)
                except InvalidAnswer as exc:
                    raise RetryablePollError('invalid_provider_answer') from exc
                logger.info('Activity %s: generated answer: %s', poll_id, text)
                result = self._submit_open_ended(poll_id, text)
            else:
                raise PollSkipped('unsupported_activity')
            self.answered_polls.add(poll_id)
            self._failures.pop(poll_id, None)
            self._retry_after.pop(poll_id, None)
            return result
        finally:
            self.in_flight_polls.discard(poll_id)

    def alive(self):
        return time.time() <= self.start_time + self.lifetime

    def _record_retry(self, poll_id: str, reason: str):
        count = self._failures.get(poll_id, 0) + 1
        self._failures[poll_id] = count
        if count >= self.retry_limit:
            self.skipped_polls.add(poll_id)
            logger.warning('Activity %s skipped after %s attempts: %s',
                           poll_id, count, reason)
            return
        delay = self.retry_backoff * (2 ** (count - 1))
        self._retry_after[poll_id] = time.monotonic() + delay
        logger.warning('Activity %s: retry %s/%s in %.1fs: %s',
                       poll_id, count + 1, self.retry_limit, delay, reason)

    def run(self):
        """Poll the host until lifetime expires, with bounded safe retries."""
        try:
            self.login()
            token = self.get_firehose_token()
        except LoginError as exc:
            logger.error('Could not start polling: %s', exc)
            return
        except (NusLoginError, ValueError, requests.RequestException,
                RetryablePollError) as exc:
            logger.error('Could not start polling: %s', type(exc).__name__)
            return

        logger.info('Polling host %s every %.1fs (answer mode: %s).',
                    self.host, self.closed_wait, self.answer_mode)
        next_status_at = time.monotonic() + _STATUS_INTERVAL
        try:
            while self.alive():
                try:
                    poll_id = self.get_new_poll_id(token)
                except LoginError as exc:
                    self._clear_idle_status()
                    logger.error('Polling stopped: %s', exc)
                    break
                if poll_id is None:
                    if not self._show_idle_status():
                        now = time.monotonic()
                        if now >= next_status_at:
                            logger.info('Still polling host %s; no new activity handled.',
                                        self.host)
                            next_status_at = now + _STATUS_INTERVAL
                    time.sleep(self.closed_wait)
                    continue
                self._clear_idle_status()
                if self._failures.get(poll_id, 0) == 0:
                    logger.info('Activity %s detected; waiting %.1fs before responding.',
                                poll_id, self.open_wait)
                    time.sleep(self.open_wait)
                if not self.alive():
                    break
                try:
                    self.answer_poll(poll_id)
                except RetryablePollError as exc:
                    self._record_retry(poll_id, str(exc))
                except PollSkipped as exc:
                    self.skipped_polls.add(poll_id)
                    logger.info('Activity %s skipped: %s', poll_id, str(exc))
                except SubmissionUncertain as exc:
                    self.skipped_polls.add(poll_id)
                    logger.warning('Activity %s submission outcome uncertain; not retrying: %s',
                                   poll_id, str(exc))
                else:
                    logger.info('Activity %s response accepted.', poll_id)
                next_status_at = time.monotonic() + _STATUS_INTERVAL
        finally:
            self._clear_idle_status()
        logger.info('Polling stopped for host %s.', self.host)
