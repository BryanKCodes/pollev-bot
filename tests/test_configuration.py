import importlib
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pollevbot.runtime_config import bot_options_from_env


class ConfigurationTests(unittest.TestCase):
    def test_polling_environment_controls_and_mode_defaults(self):
        self.assertEqual(bot_options_from_env({})['answer_mode'], 'random')
        self.assertIsNone(bot_options_from_env({'ANSWER_MODE': 'llm'},
                                                   random_max_option=3)['max_option'])
        self.assertEqual(bot_options_from_env({}, random_max_option=3)['max_option'], 3)
        options = bot_options_from_env({
            'ANSWER_MODE': ' LLM ', 'MIN_OPTION': '1', 'MAX_OPTION': '4',
            'CLOSED_WAIT': '0.5', 'OPEN_WAIT': '12',
            'POLL_REQUEST_TIMEOUT': '6', 'POLL_RETRY_LIMIT': '2',
            'POLL_RETRY_BACKOFF': '1.5',
        })
        self.assertEqual(options, {
            'answer_mode': 'llm', 'min_option': 1, 'max_option': 4,
            'closed_wait': 0.5, 'open_wait': 12.0,
            'request_timeout': 6.0, 'retry_limit': 2,
            'retry_backoff': 1.5,
        })

    def test_manifest_lists_all_model_and_polling_controls(self):
        manifest = json.loads((Path(__file__).resolve().parents[1] /
                               'app.json').read_text())
        names = {
            'ANSWER_MODE', 'MIN_OPTION', 'MAX_OPTION', 'OPEN_WAIT',
            'CLOSED_WAIT', 'POLL_REQUEST_TIMEOUT', 'POLL_RETRY_LIMIT',
            'POLL_RETRY_BACKOFF', 'LLM_BACKEND', 'LLM_MODEL_PATH',
            'LLM_CONTEXT_SIZE', 'LLM_THREADS', 'LLM_GPU_LAYERS',
            'LLM_MCQ_MAX_TOKENS', 'LLM_OPEN_MAX_TOKENS', 'LLM_TEMPERATURE',
            'LLM_INFERENCE_TIMEOUT', 'LLM_MAX_OPEN_CHARS',
            'LLM_FAILURE_POLICY', 'LLM_SEED',
        }
        self.assertTrue(names.issubset(manifest['env']))
        self.assertFalse(manifest['env']['LLM_MODEL_PATH']['required'])
        self.assertEqual(manifest['env']['LLM_FAILURE_POLICY']['value'], 'skip')

    def test_main_login_check_does_not_require_model(self):
        from pollevbot import main as launcher
        values = {'LOGIN_TYPE': 'nus', 'POLLHOST': 'private-host',
                  'ANSWER_MODE': 'llm', 'LLM_MODEL_PATH': '/missing/model.gguf'}
        instance = MagicMock()
        instance.__enter__.return_value = instance
        with patch.dict(os.environ, values, clear=True), patch.object(
                sys, 'argv', ['pollevbot.main', '--check-login']), patch.object(
                    launcher, 'load_dotenv'), patch.object(
                        launcher, 'PollBot', return_value=instance) as constructor, patch(
                            'builtins.print'):
            launcher.main()
        self.assertEqual(constructor.call_args.kwargs['answer_mode'], 'skip')
        self.assertEqual(constructor.call_args.kwargs['login_type'], 'nus')
        instance.login.assert_called_once()
        instance.get_firehose_token.assert_called_once()
        instance.get_new_poll_id.assert_called_once_with(
            instance.get_firehose_token.return_value)
        instance.run.assert_not_called()

    def test_clock_and_scheduler_launchers_use_shared_environment(self):
        values = {
            'USERNAME': 'synthetic-user', 'PASSWORD': 'synthetic-password',
            'POLLHOST': 'private-host', 'LOGIN_TYPE': 'uw', 'LIFETIME': '60',
            'DAY_OF_WEEK': 'mon', 'HOUR': '12', 'MINUTE': '0',
            'ANSWER_MODE': 'llm', 'MIN_OPTION': '2',
        }
        blocking = types.ModuleType('apscheduler.schedulers.blocking')
        blocking.BlockingScheduler = MagicMock
        modules = {
            'apscheduler': types.ModuleType('apscheduler'),
            'apscheduler.schedulers': types.ModuleType('apscheduler.schedulers'),
            'apscheduler.schedulers.blocking': blocking,
        }
        with patch.dict(os.environ, values, clear=True), patch.dict(
                sys.modules, modules):
            clock = importlib.import_module('clock')
            heroku = importlib.import_module('herokuapp')
            instance = MagicMock()
            instance.__enter__.return_value = instance
            with patch.object(clock, 'PollBot', return_value=instance) as build:
                clock.run()
                self.assertEqual(build.call_args.kwargs['answer_mode'], 'llm')
                self.assertIsNone(build.call_args.kwargs['max_option'])
                self.assertEqual(build.call_args.kwargs['min_option'], 2)
            with patch.object(heroku, 'check_day', return_value=True), patch.object(
                    heroku, 'PollBot', return_value=instance) as build:
                heroku.main()
                self.assertEqual(build.call_args.kwargs['answer_mode'], 'llm')
                self.assertIsNone(build.call_args.kwargs['max_option'])
                self.assertEqual(build.call_args.kwargs['open_wait'], 10.0)


if __name__ == '__main__':
    unittest.main()
