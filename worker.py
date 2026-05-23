"""
Legacy worker entrypoint.

The app now uses NowPayments only and does not require a separate background
worker. This process stays idle only so older deployments do not crash before
the worker service is removed from Render.
"""
from __future__ import annotations

import signal
import time


running = True


def _stop(*_args):
    global running
    running = False


def main():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while running:
        time.sleep(1)


if __name__ == '__main__':
    main()
