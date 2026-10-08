"""Private settings and browser cache for the current operating-system user."""

import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

from .model_setup import DEFAULT_MODEL_PATH, PROJECT_ROOT

ENV_PATH = PROJECT_ROOT / '.env'
# Separate OS users sharing a checkout must not inherit each other's login.
_USER_ID = hashlib.sha256(('{}:{}'.format(Path.home(),
    os.getuid() if hasattr(os, 'getuid') else os.getenv('USERNAME', 'user'))).encode()).hexdigest()[:20]
USER_DIR = PROJECT_ROOT / '.pollev-users' / _USER_ID
SETTINGS_PATH = USER_DIR / 'settings.json'
BROWSER_PROFILE = USER_DIR / 'browser'


def _private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


@contextmanager
def saved_login_lock():
    """Prevent two local runs from opening the same saved Chrome session."""
    _private_directory(USER_DIR)
    lock = (USER_DIR / 'session.lock').open('a+b')
    (USER_DIR / 'session.lock').chmod(0o600)
    locked = False
    try:
        try:
            if sys.platform == 'win32':
                import msvcrt
                if lock.seek(0, 2) == 0:
                    lock.write(b'0')
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise RuntimeError('Another bot is using your saved login. Stop it before starting again.') from exc
        yield
    finally:
        if locked:
            if sys.platform == 'win32':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def clear_saved_login():
    with saved_login_lock():
        if BROWSER_PROFILE.exists():
            shutil.rmtree(BROWSER_PROFILE)


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
        if (self.unit not in ('minutes', 'hours') or not math.isfinite(self.duration)
                or self.duration <= 0 or not math.isfinite(self.lifetime)):
            raise ValueError('Duration must be a positive number of minutes or hours.')
        if not isinstance(self.model_path, str) or not self.model_path.strip():
            raise ValueError('Select a GGUF model path.')
        self.theme = ' '.join(self.theme.split())
        if self.mode == 'theme' and not 1 <= len(self.theme) <= 120:
            raise ValueError('Enter a theme with 1 to 120 characters.')
        return self

    @classmethod
    def load(cls, path=SETTINGS_PATH):
        if path.is_file():
            try:
                data = json.loads(path.read_text())
                settings = cls(**{key: value for key, value in data.items()
                                  if key in cls.__dataclass_fields__})
                return settings.validate()
            except (ValueError, TypeError, AttributeError):
                return cls()
        # Import an old launcher's preferences once; future saves use JSON.
        values = dotenv_values(ENV_PATH)
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

    def runtime_values(self):
        values = {key: value for key, value in dotenv_values(ENV_PATH).items() if value is not None}
        values.update(os.environ)
        values.update(ANSWER_MODE=self.mode, ANSWER_THEME=self.theme,
                      LLM_MODEL_PATH=self.model_path, LLM_GPU_LAYERS=str(self.gpu_layers))
        return values

    def save(self, path=SETTINGS_PATH):
        self.validate()
        _private_directory(path.parent)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                    dir=path.parent, suffix='.json.tmp', delete=False) as output:
                temporary = Path(output.name)
                json.dump(asdict(self), output, indent=2)
                output.write('\n')
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
