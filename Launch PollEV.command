#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
    print 'Create .venv and install the dependencies listed in README.md first.'
    read 'reply?Press Enter to close.'
    exit 1
fi
.venv/bin/python -m pollevbot.gui
if [[ $? -ne 0 ]]; then
    read 'reply?Launcher failed. Press Enter to close.'
fi
