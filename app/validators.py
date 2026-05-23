"""
Validation helpers for auth, wallet, and user-facing forms.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.]{3,24}$")


class ValidationError(ValueError):
    """Raised when user input fails validation."""


@dataclass(frozen=True)
class PaginationParams:
    page: int
    per_page: int


def normalize_username(value: str) -> str:
    return (value or "").strip()


def validate_username(value: str) -> str:
    username = normalize_username(value)
    if not username:
        raise ValidationError("Username is required")
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValidationError(
            "Username must be 3-24 characters and use only letters, numbers, dots, or underscores"
        )
    return username


def validate_password(value: str, minimum_length: int = 6) -> str:
    password = value or ""
    if len(password) < minimum_length:
        raise ValidationError(f"Password must be at least {minimum_length} characters")
    return password


def validate_email(value: str | None) -> str | None:
    email = (value or "").strip().lower()
    if not email:
        return None
    if "@" not in email or "." not in email.split("@")[-1]:
        raise ValidationError("Please enter a valid email address")
    if len(email) > 120:
        raise ValidationError("Email is too long")
    return email


def validate_external_url(value: str | None, *, field_name: str = 'URL') -> str | None:
    raw = (value or '').strip()
    if not raw:
        return None

    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {'http', 'https'}:
        raise ValidationError(f'{field_name} must start with http:// or https://')
    if not parsed.netloc:
        raise ValidationError(f'{field_name} must include a valid host name')
    return raw


def validate_positive_int(value: int, field_name: str) -> int:
    if int(value or 0) <= 0:
        raise ValidationError(f"{field_name} must be greater than 0")
    return int(value)


def parse_pagination(page: int | None, per_page: int | None, max_per_page: int = 20) -> PaginationParams:
    safe_page = max(int(page or 1), 1)
    safe_per_page = min(max(int(per_page or max_per_page), 1), max_per_page)
    return PaginationParams(page=safe_page, per_page=safe_per_page)
