"""Browser-assisted check-in behavior without contacting Poll Everywhere."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import requests

from pollevbot.nus_auth import assist_host_check_in


class AssistedCheckInTests(unittest.TestCase):
    def test_waits_for_manual_confirmation_and_rechecks_feed(self):
        session = requests.Session()
        context = MagicMock()
        context.pages = []
        context.cookies.return_value = []
        browser = MagicMock()
        browser.chromium.launch_persistent_context.return_value = context
        manager = MagicMock()
        manager.__enter__.return_value = browser
        verify = Mock(side_effect=[None, ('feed-token', 'activity-id')])
        stdin = MagicMock()
        stdin.isatty.return_value = True

        with tempfile.TemporaryDirectory() as directory, patch(
                'pollevbot.nus_auth.sys.stdin', stdin), patch(
                    'playwright.sync_api.sync_playwright', return_value=manager), patch(
                        'builtins.input', side_effect=['', '']):
            result = assist_host_check_in(
                session, 'test-host', verify, profile_dir=str(Path(directory) / 'auth'))

        self.assertEqual(result, ('feed-token', 'activity-id'))
        self.assertEqual(verify.call_count, 2)
        browser.chromium.launch_persistent_context.assert_called_once()
        self.assertNotIn('geolocation',
                         browser.chromium.launch_persistent_context.call_args.kwargs)
        context.new_page.return_value.goto.assert_called_once()
        context.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
