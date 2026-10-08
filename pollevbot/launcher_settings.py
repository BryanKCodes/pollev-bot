"""Small, credential-free local launcher settings stored in .env."""

import math
import re
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import dotenv_values, set_key, unset_key

from .model_setup import DEFAULT_MODEL_PATH, PROJECT_ROOT

ENV_PATH = PROJECT_ROOT / '.env'


def normalize_host(value):
    value = value.strip()
    if '://' in value or value.startswith('pollev.com/'):
        parsed = urlparse(value if '://' in value else 'https://' + value)
        if parsed.hostname not in ('pollev.com', 'www.pollev.com'):
            raise ValueError('Enter a Poll Everywhere host or pollev.com URL.')
        value = parsed.path.strip('/')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Enter the presenter name from pollev.com/your-host.')
    return value


@dataclass
class LauncherSettings:
    host: str = ''
    mode: str = 'llm'
    theme: str = ''
    duration: float = 60
    unit: str = 'minutes'
    keep_browser_open: bool = False
    model_path: str = DEFAULT_MODEL_PATH
    gpu_layers: int = -1 if sys.platform == 'darwin' else 0

    @property
    def lifetime(self):
        return self.duration * (3600 if self.unit == 'hours' else 60)

    def validate(self):
        self.host = normalize_host(self.host)
        if self.mode not in ('llm', 'theme', 'random'):
            raise ValueError('Select LLM, Theme, or Random.')
        if self.unit not in ('minutes', 'hours') or not math.isfinite(self.duration) or self.duration <= 0:
            raise ValueError('Duration must be a positive number of minutes or hours.')
        self.theme = ' '.join(self.theme.split())
        if self.mode == 'theme' and not 1 <= len(self.theme) <= 120:
            raise ValueError('Enter a theme with 1 to 120 characters.')
        return self

    @classmethod
    def load(cls, path=ENV_PATH):
        values = dotenv_values(path)
        try:
            seconds = float(values.get('LIFETIME') or '3600')
            if not math.isfinite(seconds) or seconds <= 0:
                seconds = 3600
            unit = values.get('DURATION_UNIT')
            if unit not in ('minutes', 'hours'):
                unit = 'hours' if seconds >= 3600 and seconds % 3600 == 0 else 'minutes'
            hours = unit == 'hours'
            mode = values.get('ANSWER_MODE', 'llm')
            return cls(
                host=values.get('POLLHOST') or '',
                mode=mode if mode in ('llm', 'theme', 'random') else 'llm',
                theme=values.get('ANSWER_THEME') or '',
                duration=seconds / (3600 if hours else 60),
                unit=unit,
                keep_browser_open=(values.get('NUS_KEEP_BROWSER_OPEN') or '').lower()
                in ('true', '1', 'yes', 'on'),
                model_path=values.get('LLM_MODEL_PATH') or DEFAULT_MODEL_PATH,
                gpu_layers=int(values.get('LLM_GPU_LAYERS') or cls().gpu_layers))
        except (TypeError, ValueError):
            return cls(host=values.get('POLLHOST') or '')

    def save(self, path=ENV_PATH):
        self.validate()
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        # Remove obsolete local credentials and course-specific browser settings.
        existing = dotenv_values(path)
        for key in ('USERNAME', 'PASSWORD', 'NUS_BROWSER_PROFILE', 'DAY_OF_WEEK', 'LLM_BACKEND'):
            if key in existing:
                unset_key(str(path), key)
        values = {
            'LOGIN_TYPE': 'nus', 'POLLHOST': self.host,
            'ANSWER_MODE': self.mode, 'ANSWER_THEME': self.theme,
            'LIFETIME': format(self.lifetime, 'g'),
            'DURATION_UNIT': self.unit,
            'NUS_KEEP_BROWSER_OPEN': str(self.keep_browser_open).lower(),
            'LLM_MODEL_PATH': self.model_path, 'LLM_GPU_LAYERS': str(self.gpu_layers),
        }
        for key, value in values.items():
            set_key(str(path), key, value, quote_mode='auto')
