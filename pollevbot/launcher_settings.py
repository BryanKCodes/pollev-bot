"""Private settings and browser cache for the current operating-system user."""

import hashlib
import json
import math
import os
import platform
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

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
    gpu_layers: int = -1 if sys.platform == 'darwin' and platform.machine() == 'arm64' else 0

    @property
    def lifetime(self):
        return self.duration * (3600 if self.unit == 'hours' else 60)

    def validate(self, require_ready=True):
        if self.host.strip() or require_ready:
            self.host = normalize_host(self.host)
        else:
            self.host = ''
        if self.mode not in ('llm', 'theme', 'random'):
            raise ValueError('Select LLM, Theme, or Random.')
        if (self.unit not in ('minutes', 'hours') or not math.isfinite(self.duration)
                or self.duration <= 0 or not math.isfinite(self.lifetime)):
            raise ValueError('Duration must be a positive number of minutes or hours.')
        if not isinstance(self.model_path, str) or not self.model_path.strip():
            raise ValueError('Select a GGUF model path.')
        if isinstance(self.gpu_layers, bool) or not isinstance(self.gpu_layers, int) or self.gpu_layers < -1:
            raise ValueError('GPU layers must be -1 or a non-negative integer.')
        self.theme = ' '.join(self.theme.split())
        if len(self.theme) > 120 or (require_ready and self.mode == 'theme' and not self.theme):
            raise ValueError('Enter a theme with 1 to 120 characters.')
        return self

    @classmethod
    def load(cls, path=SETTINGS_PATH):
        if path.is_file():
            try:
                data = json.loads(path.read_text())
                settings = cls(**{key: value for key, value in data.items()
                                  if key in cls.__dataclass_fields__})
                return settings.validate(require_ready=False)
            except (ValueError, TypeError, AttributeError):
                return cls()
        return cls()

    def runtime_values(self):
        """Use saved preferences plus the provider's built-in runtime defaults."""
        return {'ANSWER_MODE': self.mode, 'ANSWER_THEME': self.theme,
                'LLM_MODEL_PATH': self.model_path, 'LLM_GPU_LAYERS': str(self.gpu_layers)}

    def save(self, path=SETTINGS_PATH):
        # Model selection can be saved before entering a host or course theme.
        self.validate(require_ready=False)
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


def save_model_selection(model_path, gpu_layers, path=SETTINGS_PATH):
    """Persist a selected/downloaded model without requiring a configured host."""
    settings = LauncherSettings.load(path)
    settings.model_path = str(model_path)
    settings.gpu_layers = gpu_layers
    settings.save(path)
