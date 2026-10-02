"""Synthetic activity fixtures; no course data or private endpoint capture."""

import unittest

from pollevbot.activity import ActivityKind, ActivityOption
from pollevbot.activity_normalization import (MULTIPLE_CHOICE_ENDPOINT,
                                             normalize_activity)
from pollevbot.answer_validation import (InvalidAnswer, candidate_options,
                                        clean_open_ended_answer,
                                        parse_option_selection,
                                        resolve_option_id)
from pollevbot.answerer import OptionSelection


class ActivityNormalizationTests(unittest.TestCase):
    def test_explicit_mcq_cleans_html_and_keeps_ordered_ids(self):
        payload = {
            'activity_type': 'MultipleChoice',
            'title': '<p>Which is <b>right</b> &amp; safe?</p>',
            'options': [
                {'id': 'opaque-a', 'text': '<span>First&nbsp;option</span>'},
                {'id': 907, 'text': 'Second &amp; final'},
            ],
        }
        activity = normalize_activity('activity-1', payload)
        self.assertEqual(activity.kind, ActivityKind.MULTIPLE_CHOICE)
        self.assertEqual(activity.question, 'Which is right & safe?')
        self.assertEqual([(o.option_id, o.text) for o in activity.options],
                         [('opaque-a', 'First option'), (907, 'Second & final')])

    def test_explicit_open_ended_is_not_inferred_from_missing_options(self):
        activity = normalize_activity('activity-2', {
            'poll_type': 'OpenEnded', 'question': '<p>Explain&nbsp;briefly</p>'})
        self.assertEqual(activity.kind, ActivityKind.OPEN_ENDED)
        self.assertEqual(activity.question, 'Explain briefly')
        self.assertEqual(activity.options, ())

    def test_unknown_and_unsupported_types_are_skipped(self):
        for kind in ('word_cloud', 'ranking', 'question_and_answer', 'new_format'):
            with self.subTest(kind=kind):
                activity = normalize_activity('activity-3', {'type': kind})
                self.assertEqual(activity.kind, ActivityKind.UNSUPPORTED)
        self.assertEqual(normalize_activity('activity-4', {
            'question': 'Text only'}).kind, ActivityKind.UNSUPPORTED)

    def test_known_mcq_endpoint_is_only_missing_metadata_fallback(self):
        payload = {'title': 'Choose', 'options': [{'id': 1, 'text': 'A'}]}
        self.assertEqual(normalize_activity(
            'activity-5', payload, successful_endpoint=MULTIPLE_CHOICE_ENDPOINT
        ).kind, ActivityKind.MULTIPLE_CHOICE)
        self.assertEqual(normalize_activity('activity-5', payload).kind,
                         ActivityKind.UNSUPPORTED)

    def test_conflicting_metadata_or_endpoint_and_malformed_options_skip(self):
        payload = {'activity_type': 'multiple_choice', 'options': [
            {'id': 1, 'text': 'A'}, {'id': 1, 'text': 'B'}]}
        self.assertEqual(normalize_activity('x', payload).kind,
                         ActivityKind.UNSUPPORTED)
        self.assertEqual(normalize_activity('x', {
            'type': 'open_ended', 'question': 'Why?'},
            successful_endpoint=MULTIPLE_CHOICE_ENDPOINT).kind,
            ActivityKind.UNSUPPORTED)
        self.assertEqual(normalize_activity('x', {
            'activity_type': 'multiple_choice',
            'options': [{'id': 1, 'text': 'A'}]},
            metadata={'activity_type': 'open_ended'}).kind,
            ActivityKind.UNSUPPORTED)


class AnswerValidationTests(unittest.TestCase):
    def setUp(self):
        self.options = (
            ActivityOption('first-id', 'First'),
            ActivityOption(88, 'Second'),
            ActivityOption('third-id', 'Third'),
            ActivityOption(99, 'Fourth'),
        )

    def test_sliced_candidate_position_maps_to_original_id(self):
        candidates = candidate_options(self.options, 1, 3)
        self.assertEqual([o.option_id for o in candidates], [88, 'third-id'])
        self.assertEqual(resolve_option_id(OptionSelection(1), candidates),
                         'third-id')
        self.assertEqual(candidate_options(self.options), self.options)

    def test_bounds_and_selection_validation(self):
        for low, high in ((-1, None), (2, 1), (True, None)):
            with self.subTest(low=low, high=high):
                with self.assertRaises(ValueError):
                    candidate_options(self.options, low, high)
        for index in (-1, 2, True, '0'):
            with self.subTest(index=index):
                with self.assertRaises(InvalidAnswer):
                    resolve_option_id(OptionSelection(index), self.options[:2])

    def test_letter_and_one_based_index_only(self):
        self.assertEqual(parse_option_selection(' B ', 3).index, 1)
        self.assertEqual(parse_option_selection('2', 3).index, 1)
        for raw in ('0', 'D', '-1', 'B.', 'option 2', '1 or 2', '', '10'):
            with self.subTest(raw=raw):
                with self.assertRaises(InvalidAnswer):
                    parse_option_selection(raw, 3)

    def test_open_ended_cleanup_and_limits(self):
        self.assertEqual(clean_open_ended_answer(
            ' **Answer:**   The result is &amp; remains clear ', 80),
            'The result is & remains clear.')
        for raw, maximum in (('', 50), ('Here is the answer.', 50),
                             ('First. Second.', 50), ('- one\n- two', 50),
                             ('A very long answer', 5), ('```bad```', 50)):
            with self.subTest(raw=raw):
                with self.assertRaises(InvalidAnswer):
                    clean_open_ended_answer(raw, maximum)


if __name__ == '__main__':
    unittest.main()
