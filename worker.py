"""
Background worker entrypoint.
Runs app context so background tasks (e.g., blockchain checker) can run independently
from web workers.
"""
from __future__ import annotations

import os
import signal
import time

from app import create_app


running = True


def _stop(*_args):
    global running
    running = False


def main():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    create_app(os.environ.get('FLASK_ENV', 'production'))
    while running:
        time.sleep(1)


if __name__ == '__main__':
    main()
