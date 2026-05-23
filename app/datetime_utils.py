"""
UTC datetime helpers without deprecated utcnow() usage.
"""
from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return a naive UTC datetime for compatibility with existing DB columns."""
    return datetime.now(UTC).replace(tzinfo=None)
