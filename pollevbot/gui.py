"""Local desktop launcher. All browser and polling work runs off the Tk thread."""

import logging
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont
from .launcher_settings import (BROWSER_PROFILE, LauncherSettings,
                                clear_saved_login, saved_login_lock, save_model_selection)
from .model_setup import DEFAULT_MODEL_PATH, download_model, resolve_model_path
from .pollbot import PollBot
from .runtime_config import answer_provider_from_env, bot_options_from_env
from .ui_icons import make_icons


class QueueLogHandler(logging.Handler):
    def __init__(self, events):
        super().__init__()
        self.events = events
        self.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s', '%H:%M:%S'))

    def emit(self, record):
        self.events.put(('log', (record.levelno, self.format(record))))


class Launcher:
    def __init__(self, root):
        self.root = root
        root.title('PollEV Bot')
        root.geometry('680x720')
        root.minsize(600, 630)
        self.events = queue.Queue()
        self.worker = None
        self.stop_event = threading.Event()
        self.confirm_event = threading.Event()
        self.closing = False
        settings = LauncherSettings.load()
        self.host = tk.StringVar(value=settings.host)
        self.mode = tk.StringVar(value=settings.mode.title() if settings.mode != 'llm' else 'LLM')
        self.theme = tk.StringVar(value=settings.theme)
        self.duration = tk.StringVar(value=format(settings.duration, 'g'))
        self.unit = tk.StringVar(value=settings.unit)
        self.keep_browser = tk.BooleanVar(value=settings.keep_browser_open)
        self.model_path = settings.model_path
        self.gpu_layers = settings.gpu_layers
        self.status = tk.StringVar(value='Ready. Choose your settings, then Start.')
        self.model_status = tk.StringVar()
        self.account_status = tk.StringVar()
        self._next_account_refresh = 0
        self.handler = QueueLogHandler(self.events)
        logging.getLogger('pollevbot').addHandler(self.handler)
        self._build()
        self.mode.trace_add('write', self._mode_changed)
        self._mode_changed()
        self._refresh_model()
        self._refresh_account()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self._drain_events)

    def _build(self):
        style = ttk.Style(self.root)
        style.configure('Title.TLabel', font=('Helvetica', 23, 'bold'))
        style.configure('Hint.TLabel', foreground='#666666')
        self.model_heading_font = tkfont.nametofont('TkDefaultFont').copy()
        self.model_heading_font.configure(weight='bold')
        style.configure('Model.TLabelframe.Label', font=self.model_heading_font)
        icon_color = style.lookup('TLabel', 'foreground') or '#555555'
        rgb = self.root.winfo_rgb(icon_color)
        icon_color = '#{:02x}{:02x}{:02x}'.format(*(channel // 257 for channel in rgb))
        self.icons = make_icons(self.root, icon_color)
        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill='both', expand=True)
        header = ttk.Frame(outer)
        header.pack(fill='x')
        ttk.Label(header, text='PollEV Bot', style='Title.TLabel').pack(side='left', anchor='n')
        account = ttk.Frame(header)
        account.pack(side='right', anchor='n')
        self.logout_button = ttk.Button(account, text='Log Out', image=self.icons['logout'],
                                        compound='left', command=self.log_out)
        self.logout_button.pack(anchor='e')
        ttk.Label(account, textvariable=self.account_status, style='Hint.TLabel',
                  wraplength=240, justify='right').pack(anchor='e', pady=(4, 0))
        form = ttk.Frame(outer)
        form.pack(fill='x', pady=(16, 0))
        form.columnconfigure(1, weight=1)
        self.inputs = []

        def label(text, row, icon):
            ttk.Label(form, text=text, image=self.icons[icon], compound='left').grid(
                row=row, column=0, sticky='w', padx=(0, 18), pady=9)

        label('Presenter', 0, 'person')
        host_entry = ttk.Entry(form, textvariable=self.host)
        host_entry.grid(row=0, column=1, sticky='ew')
        self.inputs.append((host_entry, 'normal'))
        label('Answer mode', 1, 'document')
        mode = ttk.Combobox(form, textvariable=self.mode, values=('LLM', 'Theme', 'Random'), state='readonly')
        mode.grid(row=1, column=1, sticky='ew')
        self.inputs.append((mode, 'readonly'))
        self.theme_label = ttk.Label(form, text='Course theme')
        self.theme_label.grid(row=2, column=0, sticky='w', pady=9)
        self.theme_entry = ttk.Entry(form, textvariable=self.theme)
        self.theme_entry.grid(row=2, column=1, sticky='ew')
        self.inputs.append((self.theme_entry, 'normal'))
        label('Run for', 3, 'timer')
        duration_row = ttk.Frame(form)
        duration_row.grid(row=3, column=1, sticky='ew')
        duration = ttk.Entry(duration_row, textvariable=self.duration, width=10)
        duration.pack(side='left', padx=(0, 8))
        units = ttk.Combobox(duration_row, textvariable=self.unit, values=('minutes', 'hours'),
                             state='readonly', width=12)
        units.pack(side='left')
        self.inputs.extend(((duration, 'normal'), (units, 'readonly')))
        keep = ttk.Checkbutton(outer, text='Keep the course browser open while polling', variable=self.keep_browser)
        keep.pack(anchor='w', pady=(16, 5))
        self.inputs.append((keep, 'normal'))
        model = ttk.LabelFrame(outer, text='Local model · needed for LLM and Theme',
                               padding=10, style='Model.TLabelframe')
        model.pack(fill='x', pady=16)
        ttk.Label(model, textvariable=self.model_status, wraplength=560).pack(anchor='w')
        model_actions = ttk.Frame(model)
        model_actions.pack(fill='x', pady=(8, 0))
        self.download_button = ttk.Button(model_actions, text='Download Qwen model (1.12 GB)', command=self.download)
        self.download_button.pack(side='left')
        self.browse_button = ttk.Button(model_actions, text='Choose GGUF…', command=self.browse)
        self.browse_button.pack(side='left', padx=8)
        self.inputs.append((self.browse_button, 'normal'))
        actions = ttk.Frame(outer)
        actions.pack(fill='x')
        self.start_button = ttk.Button(actions, text='Start', command=self.start,
                                       image=self.icons['play'], compound='left')
        self.start_button.pack(side='left')
        self.stop_button = ttk.Button(actions, text='Stop', command=self.stop, state='disabled',
                                      image=self.icons['stop'], compound='left')
        self.stop_button.pack(side='left', padx=8)
        self.check_button = ttk.Button(actions, text='I checked in', command=self.confirm, state='disabled')
        self.check_button.pack(side='right')
        ttk.Label(outer, textvariable=self.status, wraplength=600).pack(anchor='w', pady=14)
        log_frame = ttk.Frame(outer)
        log_frame.pack(fill='both', expand=True)
        background = style.lookup('TFrame', 'background') or self.root.cget('background')
        style.configure('Log.Vertical.TScrollbar', background=background, troughcolor=background)
        scrollbar = ttk.Scrollbar(log_frame, orient='vertical', style='Log.Vertical.TScrollbar')
        scrollbar.pack(side='right', fill='y')
        self.logs = tk.Text(log_frame, height=10, font=('Menlo', 10), wrap='word',
                            state='disabled', yscrollcommand=scrollbar.set,
                            borderwidth=0, highlightthickness=0)
        self.logs.pack(fill='both', expand=True)
        scrollbar.configure(command=self.logs.yview)
        self.logs.tag_configure('warning', background='#fff1a8', foreground='#5b4300')
        self.logs.tag_configure('error', background='#ffd8d8', foreground='#9b1c1c')
        host_entry.focus_set()

    def _mode_changed(self, *_):
        if self.mode.get() == 'Theme':
            self.theme_label.grid()
            self.theme_entry.grid()
        else:
            self.theme_label.grid_remove()
            self.theme_entry.grid_remove()

    def _refresh_model(self):
        path = resolve_model_path(self.model_path)
        ready = path.is_file() and path.suffix.lower() == '.gguf'
        self.model_status.set(('Ready: ' if ready else 'Missing: ') + path.name)
        self.download_button.configure(state='disabled' if ready else 'normal')

    def _refresh_account(self, busy=None):
        cached = BROWSER_PROFILE.is_dir()
        self.account_status.set('Browser cache present · checked on Start' if cached else 'No saved session · sign in on Start')
        if busy is None:
            busy = self.worker is not None
        self.logout_button.configure(state='normal' if cached and not busy else 'disabled')

    def _settings(self):
        try:
            duration = float(self.duration.get())
        except ValueError as exc:
            raise ValueError('Enter a number for the duration.') from exc
        return LauncherSettings(self.host.get(), self.mode.get().lower(), self.theme.get(),
                                duration, self.unit.get(), self.keep_browser.get(),
                                self.model_path, self.gpu_layers).validate()

    def browse(self):
        path = filedialog.askopenfilename(parent=self.root, title='Choose a GGUF instruct model',
                                          filetypes=(('GGUF model', '*.gguf'),))
        if path:
            try:
                save_model_selection(path, self.gpu_layers)
            except (ValueError, OSError) as exc:
                messagebox.showerror('Could not save model', str(exc), parent=self.root)
                return
            self.model_path = path
            self._refresh_model()
            self.status.set('Model selected and saved.')

    def log_out(self):
        if self.worker is not None:
            return
        try:
            clear_saved_login()
            self._refresh_account()
            self.status.set('Logged out of the bot. Start to sign in again.')
        except (RuntimeError, OSError) as exc:
            messagebox.showerror('Could not clear login', str(exc), parent=self.root)

    def _busy(self, busy):
        for widget, state in self.inputs:
            widget.configure(state='disabled' if busy else state)
        self.start_button.configure(state='disabled' if busy else 'normal')
        self.stop_button.configure(state='normal' if busy else 'disabled')
        self._refresh_account(busy)
        if busy:
            self.download_button.configure(state='disabled')
        else:
            self._refresh_model()

    def start(self):
        if self.worker is not None:
            return
        try:
            settings = self._settings()
            if settings.mode != 'random':
                from .llama_cpp_provider import LlamaCppConfig
                LlamaCppConfig.from_env(settings.runtime_values())
                import llama_cpp  # Confirm the inference dependency before sign-in.
            import playwright.sync_api
            settings.save()
        except (ValueError, OSError, ImportError) as exc:
            messagebox.showerror('Could not start', str(exc), parent=self.root)
            return
        self.stop_event.clear()
        self.confirm_event.clear()
        self._busy(True)
        self.status.set('Checking saved login…')
        self.worker = threading.Thread(target=self._run_bot, args=(settings,), daemon=True)
        self.worker.start()

    def _run_bot(self, settings):
        awake = None
        try:
            if sys.platform == 'darwin':
                try:
                    awake = subprocess.Popen(['caffeinate', '-i'])
                except OSError:
                    logging.getLogger(__name__).warning('Keep your computer awake while polling.')
            values = settings.runtime_values()
            options = bot_options_from_env(values)
            provider = answer_provider_from_env(values)
            with saved_login_lock():
                with PollBot('', '', settings.host, login_type='nus', lifetime=settings.lifetime,
                             browser_profile=str(BROWSER_PROFILE), answer_provider=provider,
                             keep_browser_open=settings.keep_browser_open,
                             login_timeout=float(values.get('NUS_LOGIN_TIMEOUT', '300')),
                             stop_event=self.stop_event,
                             status_callback=lambda text: self.events.put(('status', text)),
                             action_callback=lambda text: self.events.put(('action', text)),
                             check_in_confirm=self._wait_for_check_in, **options) as bot:
                    bot.run()
        except Exception as exc:
            self.events.put(('log', (logging.ERROR, 'Could not run: {}'.format(exc))))
        finally:
            if awake is not None:
                awake.terminate()
                awake.wait()
            self.events.put(('done', 'Stopped. See the event log for details.'))

    def _wait_for_check_in(self, deadline):
        self.confirm_event.clear()
        self.events.put(('check_in', 'Complete check-in in Chrome, then click “I checked in”.'))
        while not self.stop_event.is_set() and time.monotonic() < deadline:
            if self.confirm_event.wait(min(0.2, max(0, deadline - time.monotonic()))):
                self.events.put(('check_done', 'Checking course access…'))
                return True
        self.events.put(('check_done', 'Check-in cancelled or timed out.'))
        return False

    def confirm(self):
        self.check_button.configure(state='disabled')
        self.confirm_event.set()

    def stop(self):
        self.stop_event.set()
        self.status.set('Stopping… waiting for the current browser, request, or generation to finish.')
        self.stop_button.configure(state='disabled')
        self.check_button.configure(state='disabled')

    def download(self):
        if self.worker is not None:
            return
        self.stop_event.clear()
        self._busy(True)
        self.status.set('Downloading Qwen from its publisher…')

        def run():
            try:
                download_model(lambda size, total: self.events.put(('status',
                    'Downloading model: {:.0f} MB{}'.format(size / 1_000_000,
                    ' / {:.0f} MB'.format(total / 1_000_000) if total else ''))), self.stop_event)
                save_model_selection(DEFAULT_MODEL_PATH, self.gpu_layers)
                self.events.put(('model_ready', DEFAULT_MODEL_PATH))
                self.events.put(('done', 'Model verified, selected, and saved. Ready to Start.'))
            except Exception as exc:
                self.events.put(('log', (logging.ERROR, str(exc))))
                self.events.put(('done', 'Model download stopped.'))
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _drain_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'log':
                    level, text = value
                    tag = 'error' if level >= logging.ERROR else 'warning' if level >= logging.WARNING else ''
                    self.logs.configure(state='normal')
                    self.logs.insert('end', text + '\n', (tag,) if tag else ())
                    # Keep a bounded event history for long sessions.
                    lines = int(self.logs.index('end-1c').split('.')[0])
                    if lines > 600:
                        self.logs.delete('1.0', '{}.0'.format(lines - 500))
                    self.logs.see('end')
                    self.logs.configure(state='disabled')
                elif kind in ('status', 'check_in', 'check_done'):
                    self.status.set(value)
                    if kind == 'check_in':
                        self.check_button.configure(state='normal')
                    elif kind == 'check_done':
                        self.check_button.configure(state='disabled')
                elif kind == 'action':
                    self.status.set(value)
                elif kind == 'model_ready':
                    self.model_path = value
                elif kind == 'done':
                    self.worker = None
                    self._busy(False)
                    self.check_button.configure(state='disabled')
                    self.status.set(value)
        except queue.Empty:
            pass
        if time.monotonic() >= self._next_account_refresh:
            self._refresh_account()
            self._next_account_refresh = time.monotonic() + 2
        if self.closing and self.worker is None:
            logging.getLogger('pollevbot').removeHandler(self.handler)
            self.root.destroy()
            return
        self.root.after(100, self._drain_events)

    def close(self):
        self.closing = True
        if self.worker is not None:
            self.stop()
        else:
            logging.getLogger('pollevbot').removeHandler(self.handler)
            self.root.destroy()


def main():
    root = tk.Tk()
    Launcher(root)
    root.mainloop()


if __name__ == '__main__':
    main()
