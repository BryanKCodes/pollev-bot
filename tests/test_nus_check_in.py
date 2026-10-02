"""Browser-assisted check-in behavior without contacting Poll Everywhere."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import requests

from pollevbot.nus_auth import NusHostBrowser, assist_host_check_in


class AssistedCheckInTests(unittest.TestCase):
    def test_waits_for_manual_confirmation_and_rechecks_feed(self):
        session = requests.Session()
        context = MagicMock()
        context.pages = []
        context.cookies.return_value = []
        browser = MagicMock()
        browser.chromium.launch_persistent_context.return_value = context
        manager = MagicMock()
        manager.start.return_value = browser
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
        browser.stop.assert_called_once()

    def test_visible_browser_stays_open_and_syncs_changed_cookies(self):
        session = requests.Session()
        context = MagicMock()
        context.pages = []
        initial = {'domain': '.pollev.com', 'path': '/', 'name': 'polleverywhere_session_id',
                   'value': 'first', 'secure': True, 'expires': -1}
        changed = dict(initial, value='second')
        context.cookies.side_effect = [[initial], [initial], [changed]]
        browser = MagicMock()
        browser.chromium.launch_persistent_context.return_value = context
        manager = MagicMock()
        manager.start.return_value = browser

        with tempfile.TemporaryDirectory() as directory, patch(
                'playwright.sync_api.sync_playwright', return_value=manager):
            with NusHostBrowser(session, 'test-host', str(Path(directory) / 'auth')) as host:
                self.assertEqual(session.cookies.get('polleverywhere_session_id'), 'first')
                self.assertEqual(context.close.call_count, 0)
                host.sync_cookies()
                host.sync_cookies()
                self.assertEqual(session.cookies.get('polleverywhere_session_id'), 'second')
            context.close.assert_called_once()
            browser.stop.assert_called_once()


if __name__ == '__main__':
    unittest.main()
