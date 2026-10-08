"""Project-relative model paths and a verified publisher download."""

import hashlib
import os
from pathlib import Path
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_NAME = 'qwen2.5-1.5b-instruct-q4_k_m.gguf'
DEFAULT_MODEL_PATH = 'models/' + MODEL_NAME
MODEL_URL = ('https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/'
             'resolve/main/' + MODEL_NAME)
MODEL_SHA256 = '6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e'


def resolve_model_path(value=DEFAULT_MODEL_PATH):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def download_model(progress=None, cancel=None):
    """Download the default Qwen file, publishing it only after SHA256 passes."""
    destination = resolve_model_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix('.gguf.part')
    digest = hashlib.sha256()
    downloaded = 0
    try:
        request = Request(MODEL_URL, headers={'User-Agent': 'pollev-bot-model-setup'})
        with urlopen(request, timeout=30) as response, partial.open('wb') as output:
            total = int(response.headers.get('Content-Length', '0'))
            while True:
                if cancel is not None and cancel.is_set():
                    raise InterruptedError('Model download cancelled.')
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                if progress:
                    progress(downloaded, total)
        if digest.hexdigest() != MODEL_SHA256:
            raise ValueError('Model checksum mismatch; the download was discarded.')
        os.replace(partial, destination)
        return destination
    finally:
        partial.unlink(missing_ok=True)


if __name__ == '__main__':
    print('Downloading Qwen2.5 1.5B Q4_K_M (about 1.12 GB) from its publisher…')
    model_path = download_model()
    from .launcher_settings import LauncherSettings, save_model_selection
    save_model_selection(DEFAULT_MODEL_PATH, LauncherSettings.load().gpu_layers)
    print('Model ready and selected:', model_path)
