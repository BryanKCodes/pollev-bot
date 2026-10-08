# PollEV Bot

A desktop app that watches a Poll Everywhere presenter and automatically answers supported multiple-choice and text activities. Built for NUS sign-in, with manual MFA and an optional local AI model.

## Answer modes

| Mode | What it does |
| --- | --- |
| **LLM** | Uses local AI to answer visible questions and write short, natural replies. Recommended when questions are visible. |
| **Theme** | Picks random MCQ options and writes 2–3 word replies about your chosen topic. Useful when questions are hidden. |
| **Random** | Picks random MCQ options and replies `yes.` to text questions. No model needed. |

## Get started

You need **Google Chrome** and **Python 3.10+ with Tkinter**. Clone this repository and open a terminal in its folder.

### Apple Silicon Mac

With Homebrew installed:

```sh
brew install python@3.12 python-tk@3.12
python3.12 -m venv .venv
source .venv/bin/activate
CMAKE_ARGS='-DGGML_METAL=on' python -m pip install -r requirements-desktop.txt
python -m pollevbot.gui
```

After setup, you can also double-click **Launch PollEV.command**.

### Other computers

Use a Python installation with Tkinter:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-desktop.txt
python -m pollevbot.gui
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` instead. On Linux, Tkinter may need the `python3-tk` package. Installing the local AI dependency may require a C/C++ compiler.

**Only want Random mode?** Install `requirements-nus.txt` instead of `requirements-desktop.txt` to skip the AI dependency entirely.

## Using the app

1. Enter the **Presenter** name or full `pollev.com/host` URL.
2. Choose an answer mode and how long to run. For Theme, enter a topic such as `NLP transformers`.
3. For LLM or Theme, click **Download Qwen model** once (about 1.12 GB), or **Choose GGUF** to select your own model. The download automatically selects the model.
4. Click **Start**. On your first run, Chrome opens for NUS sign-in and MFA. Complete any respondent-name prompt too.
5. If the course requires check-in, complete it in Chrome, allow location if requested, then click **I checked in**.
6. Watch the updating status and activity logs. Click **Stop** to finish early.

Settings and login sessions are saved locally for your OS user; **no `.env` file is needed**. Use **Log Out** to clear the saved session. Models, settings, and browser caches are ignored by Git.

Keep your computer awake and connected to the internet. The Mac launcher prevents idle sleep while running, but closing the lid or manually sleeping still interrupts it.
