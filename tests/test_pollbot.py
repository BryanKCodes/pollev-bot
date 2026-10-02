"""HTTP-isolated dispatch tests using synthetic, non-course payloads."""

import json
import random
import unittest
from unittest.mock import patch

import requests

from pollevbot.answer_providers import FakeAnswerProvider, LegacyRandomProvider
from pollevbot.llama_cpp_provider import AnswerUnavailable
from pollevbot.open_ended_transport import OpenEndedTransport
from pollevbot.pollbot import (LoginError, PollBot, PollSkipped, RetryablePollError,
                              SubmissionUncertain)


def response(status=200, payload=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps({} if payload is None else payload).encode()
    result.url = 'https://fixture.invalid/'
    return result


MCQ = {
    'activity_type': 'multiple_choice',
    'title': '<p>Choose &amp; explain?</p>',
    'options': [
        {'id': 'opaque-first', 'text': 'First'},
        {'id': 201, 'text': 'Second'},
        {'id': 'opaque-third', 'text': 'Third'},
        {'id': 401, 'text': 'Fourth'},
    ],
}


class FakeSession:
    def __init__(self, get_results=(), post_results=()):
        self.get_results = list(get_results)
        self.post_results = list(post_results)
        self.calls = []
        self.cookies = requests.cookies.RequestsCookieJar()

    @staticmethod
    def _take(results):
        if not results:
            raise AssertionError('Unexpected HTTP request')
        item = results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kwargs):
        self.calls.append(('GET', url, kwargs))
        return self._take(self.get_results)

    def post(self, url, **kwargs):
        self.calls.append(('POST', url, kwargs))
        return self._take(self.post_results)

    def close(self):
        pass


class SyntheticOpenEndedTransport(OpenEndedTransport):
    """Fixture-only route and field; these are not Poll Everywhere claims."""

    def fetch(self, session, activity_id, timeout):
        result = session.get('https://fixture.invalid/text/{}'.format(activity_id),
                             timeout=timeout, allow_redirects=False)
        result.raise_for_status()
        return result.json()

    def submit(self, session, activity_id, text, csrf_token, timeout):
        return session.post(
            'https://fixture.invalid/text/{}/responses'.format(activity_id),
            headers={'x-csrf-token': csrf_token}, data={'text': text},
            timeout=timeout, allow_redirects=False)


class FlakyProvider(FakeAnswerProvider):
    def __init__(self):
        super().__init__(choice_index=0)
        self.calls = 0

    def select_option(self, question, options):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError('model unavailable')
        return super().select_option(question, options)


class FailedModelProvider(FakeAnswerProvider):
    def select_option(self, question, options):
        raise AnswerUnavailable('synthetic model failure')


class PollBotDispatchTests(unittest.TestCase):
    def make_bot(self, session, **options):
        bot = PollBot('synthetic-user', 'synthetic-password', 'test-host',
                      login_type='pollev', request_timeout=7, **options)
        bot.session.close()
        bot.session = session
        return bot

    def test_mcq_uses_filtered_position_original_id_csrf_and_timeouts(self):
        session = FakeSession(
            get_results=[response(payload=MCQ), response(payload={'token': 'csrf-fixture'})],
            post_results=[response(payload={'success': True})])
        provider = FakeAnswerProvider(choice_index=1)
        bot = self.make_bot(session, answer_provider=provider,
                            min_option=1, max_option=3)
        with self.assertLogs('pollevbot.pollbot', level='INFO') as logged:
            self.assertEqual(bot.answer_poll('poll-1'), {'success': True})
        self.assertTrue(any('question: Choose & explain?' in line
                            for line in logged.output))
        self.assertEqual(provider.choice_requests,
                         [('Choose & explain?', ('Second', 'Third'))])
        self.assertEqual(session.calls[2][2]['data']['option_id'], 'opaque-third')
        self.assertEqual(session.calls[2][2]['headers']['x-csrf-token'],
                         'csrf-fixture')
        self.assertEqual(session.calls[2][2]['data']['source'], 'pollev_page')
        self.assertEqual([call[2]['timeout'] for call in session.calls], [7, 7, 7])
        self.assertFalse(session.calls[2][2]['allow_redirects'])
        self.assertEqual(bot.answered_polls, {'poll-1'})
        with self.assertRaises(PollSkipped):
            bot.answer_poll('poll-1')
        self.assertEqual(len(session.calls), 3)

    def test_missing_course_feed_token_is_valid_while_idle(self):
        session = FakeSession(get_results=[response(payload={'firehose_token': None})])
        bot = self.make_bot(session)
        self.assertIsNone(bot.get_firehose_token())

    def test_registration_flags_alone_do_not_imply_live_denial(self):
        session = FakeSession(get_results=[response(payload={
            'firehose_token': None,
            'registration_suggested': True,
            'participant_self_registration': False})])
        bot = self.make_bot(session)
        self.assertIsNone(bot.get_firehose_token())

    def test_feed_subscription_denial_is_not_treated_as_idle(self):
        session = FakeSession(get_results=[response(payload={
            'message': json.dumps({'error': {
                'type': 'UnauthorizedSubscription',
                'message': 'You are not authorized to subscribe'}})})])
        bot = self.make_bot(session)
        with self.assertRaisesRegex(LoginError, 'rejected this account'):
            bot.get_new_poll_id('fixture-token')

    def test_other_feed_error_is_reported_without_activity(self):
        session = FakeSession(get_results=[response(payload={
            'message': json.dumps({'error': {'type': 'TemporaryFailure'}})})])
        bot = self.make_bot(session)
        with self.assertLogs('pollevbot.pollbot', level='WARNING') as logged:
            self.assertIsNone(bot.get_new_poll_id('fixture-token'))
        self.assertTrue(any('TemporaryFailure' in line for line in logged.output))

    def test_open_ended_dispatch_uses_injected_transport_and_clean_text(self):
        session = FakeSession(
            get_results=[response(404), response(payload={
                'type': 'open_ended', 'question': 'Why is this useful?'}),
                response(payload={'token': 'text-csrf'})],
            post_results=[response(payload={'success': True})])
        provider = FakeAnswerProvider(open_ended_text='**Answer:**  It helps.  ')
        bot = self.make_bot(session, answer_provider=provider,
                            open_ended_transport=SyntheticOpenEndedTransport())
        self.assertEqual(bot.answer_poll('poll-2'), {'success': True})
        self.assertEqual(provider.open_ended_requests, ['Why is this useful?'])
        self.assertEqual(session.calls[3][1],
                         'https://fixture.invalid/text/poll-2/responses')
        self.assertEqual(session.calls[3][2]['data'], {'text': 'It helps.'})
        self.assertEqual(session.calls[3][2]['headers']['x-csrf-token'],
                         'text-csrf')
        self.assertTrue(all(call[2]['timeout'] == 7 for call in session.calls))

    def test_open_ended_without_verified_transport_never_submits(self):
        session = FakeSession(get_results=[response(404)])
        bot = self.make_bot(session, answer_provider=FakeAnswerProvider())
        with self.assertRaises(PollSkipped):
            bot.answer_poll('poll-3')
        self.assertEqual(len(session.calls), 1)
        self.assertFalse(bot.answered_polls)

    def test_unsupported_activity_and_invalid_choice_never_submit(self):
        session = FakeSession(get_results=[response(payload={
            'type': 'word_cloud', 'question': 'A word?'}), response(payload=MCQ)])
        bot = self.make_bot(session, answer_provider=FakeAnswerProvider(choice_index=99))
        with self.assertRaises(PollSkipped):
            bot.answer_poll('poll-4')
        with self.assertRaises(RetryablePollError):
            bot.answer_poll('poll-5')
        self.assertEqual([call[0] for call in session.calls], ['GET', 'GET'])
        self.assertFalse(bot.answered_polls)

    def test_failed_provider_can_retry_without_random_submission_or_lost_id(self):
        session = FakeSession(get_results=[response(payload=MCQ),
                                           response(payload={'message': json.dumps({'uid': 'poll-6'})}),
                                           response(payload=MCQ),
                                           response(payload={'token': 'csrf'})],
                              post_results=[response(payload={'success': True})])
        provider = FlakyProvider()
        bot = self.make_bot(session, answer_provider=provider, retry_backoff=0)
        with self.assertRaises(RetryablePollError):
            bot.answer_poll('poll-6')
        self.assertFalse(bot.answered_polls)
        self.assertFalse(bot.attempted_polls)
        bot._record_retry('poll-6', 'answer_generation_failed')
        self.assertEqual(bot.get_new_poll_id(), 'poll-6')
        self.assertEqual(bot.answer_poll('poll-6'), {'success': True})
        self.assertEqual(provider.calls, 2)
        self.assertEqual(len([call for call in session.calls if call[0] == 'POST']), 1)
        self.assertEqual(bot.answered_polls, {'poll-6'})

    def test_model_failure_never_falls_back_to_random_implicitly(self):
        session = FakeSession(get_results=[response(payload=MCQ)])
        bot = self.make_bot(session, answer_mode='llm',
                            answer_provider=FailedModelProvider())
        with self.assertRaises(RetryablePollError):
            bot.answer_poll('model-failure')
        self.assertEqual([call[0] for call in session.calls], ['GET'])
        self.assertFalse(bot.attempted_polls)

    def test_explicit_submission_error_is_not_recorded_as_answered(self):
        session = FakeSession(
            get_results=[response(payload=MCQ), response(payload={'token': 'csrf'})],
            post_results=[response(payload={'success': False,
                                            'error': 'fixture rejected'})])
        bot = self.make_bot(session, answer_provider=FakeAnswerProvider())
        with self.assertRaises(SubmissionUncertain):
            bot.answer_poll('rejected')
        self.assertNotIn('rejected', bot.answered_polls)
        self.assertIn('rejected', bot.attempted_polls)

    def test_csrf_failure_is_retryable_before_submit(self):
        session = FakeSession(get_results=[response(payload=MCQ), response(503)])
        bot = self.make_bot(session, answer_provider=FakeAnswerProvider())
        with self.assertRaises(RetryablePollError):
            bot.answer_poll('poll-7')
        self.assertFalse(bot.attempted_polls)
        self.assertFalse(bot.answered_polls)
        self.assertEqual([call[0] for call in session.calls], ['GET', 'GET'])

    def test_transient_activity_fetch_can_retry_before_any_submission(self):
        session = FakeSession(
            get_results=[requests.Timeout('fixture fetch timeout'),
                         response(payload=MCQ), response(payload={'token': 'csrf'})],
            post_results=[response(payload={'success': True})])
        bot = self.make_bot(session, answer_provider=FakeAnswerProvider(),
                            retry_backoff=0)
        with self.assertRaises(RetryablePollError):
            bot.answer_poll('fetch-retry')
        self.assertFalse(bot.answered_polls)
        self.assertFalse(bot.attempted_polls)
        bot._record_retry('fetch-retry', 'activity_fetch_failed')
        bot.answer_poll('fetch-retry')
        self.assertEqual(bot.answered_polls, {'fetch-retry'})
        self.assertEqual(len([call for call in session.calls if call[0] == 'POST']), 1)

    def test_uncertain_submission_is_never_repeated(self):
        session = FakeSession(
            get_results=[response(payload=MCQ), response(payload={'token': 'csrf'})],
            post_results=[requests.Timeout('fixture timeout')])
        bot = self.make_bot(session, answer_provider=FakeAnswerProvider())
        with self.assertRaises(SubmissionUncertain):
            bot.answer_poll('poll-8')
        self.assertEqual(bot.attempted_polls, {'poll-8'})
        self.assertFalse(bot.answered_polls)
        with self.assertRaises(SubmissionUncertain):
            bot.answer_poll('poll-8')
        self.assertEqual(len([call for call in session.calls if call[0] == 'POST']), 1)

    def test_explicit_rate_limit_can_retry_with_backoff(self):
        session = FakeSession(get_results=[
            response(payload=MCQ), response(payload={'token': 'csrf'}),
            response(payload=MCQ), response(payload={'token': 'csrf'})],
            post_results=[response(429), response(payload={'success': True})])
        bot = self.make_bot(session, answer_provider=FakeAnswerProvider(),
                            retry_backoff=0)
        with self.assertRaises(RetryablePollError):
            bot.answer_poll('poll-9')
        self.assertFalse(bot.attempted_polls)
        bot._record_retry('poll-9', 'submission_rate_limited')
        bot.answer_poll('poll-9')
        self.assertEqual(bot.answered_polls, {'poll-9'})
        self.assertEqual(len([call for call in session.calls if call[0] == 'POST']), 2)

    def test_retry_budget_is_bounded(self):
        bot = self.make_bot(FakeSession(), retry_limit=2)
        bot._record_retry('poll-10', 'transient')
        self.assertNotIn('poll-10', bot.skipped_polls)
        bot._record_retry('poll-10', 'transient')
        self.assertIn('poll-10', bot.skipped_polls)
        self.assertNotIn('poll-10', bot.answered_polls)

    def test_random_mode_uses_legacy_uniform_candidate_choice(self):
        session = FakeSession(
            get_results=[response(payload=MCQ), response(payload={'token': 'csrf'})],
            post_results=[response(payload={'success': True})])
        bot = self.make_bot(session, answer_mode='random', min_option=1,
                            max_option=3)
        bot.answer_provider = LegacyRandomProvider(random.Random(0))
        bot.answer_poll('poll-11')
        self.assertEqual(session.calls[2][2]['data']['option_id'], 'opaque-third')

    def test_skip_mode_never_fetches_or_submits(self):
        session = FakeSession()
        bot = self.make_bot(session, answer_mode='skip')
        with self.assertRaises(PollSkipped):
            bot.answer_poll('poll-12')
        self.assertEqual(session.calls, [])

    def test_run_retries_generation_failure_then_accepts_response(self):
        session = FakeSession(get_results=[response(payload=MCQ),
                                           response(payload=MCQ),
                                           response(payload={'token': 'csrf'})],
                              post_results=[response(payload={'success': True})])
        bot = self.make_bot(session, answer_provider=FlakyProvider(),
                            retry_backoff=0, open_wait=0)
        with patch.object(bot, 'login'), patch.object(bot, 'get_firehose_token',
                                                     return_value='token'), patch.object(
            bot, 'get_new_poll_id', side_effect=['poll-13', 'poll-13']), patch.object(
                bot, 'alive', side_effect=[True, True, True, True, False]), patch(
                    'pollevbot.pollbot.time.sleep'):
            bot.run()
        self.assertEqual(bot.answered_polls, {'poll-13'})
        self.assertEqual(len([call for call in session.calls if call[0] == 'POST']), 1)

    def test_nus_run_opens_browser_for_live_feed_check_in(self):
        bot = PollBot('synthetic-user', '', 'test-host', login_type='nus',
                      answer_mode='skip')
        with patch.object(bot, 'login'), patch.object(
                bot, 'get_firehose_token', return_value=None) as tokens, patch.object(
                    bot, 'get_new_poll_id', side_effect=[
                        LoginError('fixture denied'), None]), patch.object(
                            bot, 'alive', side_effect=[True, True, False]), patch(
                                'pollevbot.pollbot.time.sleep'), patch(
                                    'pollevbot.nus_auth.assist_host_check_in',
                                    return_value=(None, None)) as assist:
            bot.run()
        self.assertEqual(tokens.call_count, 3)
        assist.assert_called_once()


if __name__ == '__main__':
    unittest.main()
