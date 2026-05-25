"""
Security utilities for request guarding, redirects, and session handling.
"""
from __future__ import annotations

import logging
import time
from typing import Optional
from urllib.parse import urljoin, urlsplit

from flask import abort, current_app, has_request_context, request, session, url_for
from flask_login import current_user

from app.extensions import cache


SUSPICIOUS_REQUEST_PATTERNS = (
    '../',
    '..\\',
    '%2e%2e',
    '%252e%252e',
    '<script',
    'union select',
    'sleep(',
    'benchmark(',
    '/etc/passwd',
    'cmd.exe',
    'powershell',
    'php://',
    'file://',
    '%00',
)


def _get_security_logger():
    return current_app.extensions.get('security_logger') or current_app.logger


def log_security_event(event_type: str, message: str, *, level: int = logging.WARNING, **context) -> None:
    """Write a structured security log entry."""
    ip = (_get_client_ip() or '-') if has_request_context() else '-'
    method = request.method if has_request_context() else '-'
    path = request.path if has_request_context() else '-'
    details = ' '.join(f'{key}={value!r}' for key, value in sorted(context.items()) if value not in (None, ''))
    log_line = f'event={event_type} ip={ip} method={method} path={path!r} message={message!r}'
    if details:
        log_line = f'{log_line} {details}'
    _get_security_logger().log(level, log_line)


def _get_client_ip() -> Optional[str]:
    """Resolve client IP address with proxy headers support."""
    if current_app.config.get('RATE_LIMIT_TRUST_PROXY_HEADERS', False):
        forwarded = request.headers.get('X-Forwarded-For', '')
        if forwarded:
            ip = forwarded.split(',')[0].strip()
            if ip:
                return ip
        real_ip = request.headers.get('X-Real-IP', '').strip()
        if real_ip:
            return real_ip
    return request.remote_addr


def _incr_with_ttl(key: str, ttl_seconds: int) -> int:
    """Increment a counter with TTL, using Redis if available."""
    redis_client = current_app.extensions.get('redis_client')
    if redis_client is not None:
        try:
            count = redis_client.incr(key)
            if count == 1:
                redis_client.expire(key, ttl_seconds)
            return int(count)
        except Exception as exc:
            current_app.logger.warning(f'Redis unavailable for security counter; falling back to cache: {exc}')

    count = cache.get(key) or 0
    count += 1
    cache.set(key, count, timeout=ttl_seconds)
    return int(count)


def _set_with_ttl(key: str, value, ttl_seconds: int) -> None:
    redis_client = current_app.extensions.get('redis_client')
    if redis_client is not None:
        try:
            redis_client.setex(key, ttl_seconds, value)
            return
        except Exception as exc:
            current_app.logger.warning(f'Redis unavailable for security setex; falling back to cache: {exc}')
    cache.set(key, value, timeout=ttl_seconds)


def _get_value(key: str):
    redis_client = current_app.extensions.get('redis_client')
    if redis_client is not None:
        try:
            return redis_client.get(key)
        except Exception as exc:
            current_app.logger.warning(f'Redis unavailable for security get; falling back to cache: {exc}')
    return cache.get(key)


def _delete_key(key: str) -> None:
    redis_client = current_app.extensions.get('redis_client')
    if redis_client is not None:
        try:
            redis_client.delete(key)
            return
        except Exception as exc:
            current_app.logger.warning(f'Redis unavailable for security delete; falling back to cache: {exc}')
    cache.delete(key)


def _is_endpoint_exempt() -> bool:
    endpoint = request.endpoint or ''
    return endpoint in current_app.config.get('RATE_LIMIT_EXEMPT_ENDPOINTS', ())


def enforce_rate_limit() -> None:
    """Apply per-IP and per-user rate limits."""
    if not current_app.config.get('RATE_LIMIT_ENABLED', True):
        return
    if _is_endpoint_exempt():
        return

    window = int(current_app.config.get('RATE_LIMIT_WINDOW_SECONDS', 60))
    per_ip = int(current_app.config.get('RATE_LIMIT_PER_IP', 180))
    per_user = int(current_app.config.get('RATE_LIMIT_PER_USER', 120))
    bucket = int(time.time()) // max(1, window)

    if per_ip > 0:
        ip = _get_client_ip()
        if ip:
            key = f'rl:ip:{ip}:{bucket}'
            if _incr_with_ttl(key, window) > per_ip:
                log_security_event('rate_limit_ip', 'Per-IP rate limit exceeded')
                abort(429)

    if per_user > 0 and current_user.is_authenticated:
        key = f'rl:user:{current_user.id}:{bucket}'
        if _incr_with_ttl(key, window) > per_user:
            log_security_event('rate_limit_user', 'Per-user rate limit exceeded', user_id=current_user.id)
            abort(429)


def enforce_request_guards() -> None:
    """Block obviously malicious request patterns before route handlers run."""
    if _is_endpoint_exempt():
        return

    ip = _get_client_ip()
    if ip:
        lock_key = f'suspicious:lock:{ip}'
        if _get_value(lock_key):
            log_security_event('suspicious_request_locked', 'Suspicious request lock active')
            abort(429)

    request_target = request.full_path or request.path or ''
    user_agent = request.headers.get('User-Agent', '')
    combined = f'{request_target} {user_agent}'.lower()
    matches = [pattern for pattern in SUSPICIOUS_REQUEST_PATTERNS if pattern in combined]
    if not matches:
        return

    log_security_event(
        'suspicious_request',
        'Blocked suspicious request pattern',
        matches=','.join(matches),
        user_agent=user_agent[:200],
    )

    if ip:
        window = int(current_app.config.get('SUSPICIOUS_REQUEST_WINDOW_SECONDS', 600))
        limit = int(current_app.config.get('SUSPICIOUS_REQUEST_LIMIT', 6))
        lockout = int(current_app.config.get('SUSPICIOUS_REQUEST_LOCKOUT_SECONDS', 1800))
        count = _incr_with_ttl(f'suspicious:count:{ip}', window)
        if count >= limit:
            _set_with_ttl(f'suspicious:lock:{ip}', 1, lockout)
    abort(400)


def _normalize_identity(value: str | None) -> str:
    return (value or '').strip().lower()


def _auth_lock_key(kind: str, value: str) -> str:
    return f'auth:lock:{kind}:{value}'


def _auth_fail_key(kind: str, value: str) -> str:
    return f'auth:fail:{kind}:{value}'


def is_auth_throttled(username: str | None = None) -> bool:
    """Return True when the IP or username is currently locked out."""
    ip = _get_client_ip()
    normalized_username = _normalize_identity(username)
    keys = []
    if ip:
        keys.append(_auth_lock_key('ip', ip))
    if normalized_username:
        keys.append(_auth_lock_key('user', normalized_username))
    return any(bool(_get_value(key)) for key in keys)


def register_auth_failure(username: str | None = None) -> None:
    """Track failed login attempts by IP and username."""
    window = int(current_app.config.get('BRUTE_FORCE_WINDOW_SECONDS', 900))
    max_attempts = int(current_app.config.get('BRUTE_FORCE_MAX_ATTEMPTS', 8))
    lockout = int(current_app.config.get('BRUTE_FORCE_LOCKOUT_SECONDS', 1800))
    ip = _get_client_ip()
    normalized_username = _normalize_identity(username)

    counters = []
    if ip:
        counters.append(('ip', ip))
    if normalized_username:
        counters.append(('user', normalized_username))

    for kind, value in counters:
        count = _incr_with_ttl(_auth_fail_key(kind, value), window)
        if count >= max_attempts:
            _set_with_ttl(_auth_lock_key(kind, value), 1, lockout)

    log_security_event('auth_failure', 'Failed authentication attempt', username=normalized_username)


def clear_auth_failures(username: str | None = None) -> None:
    """Clear failed login counters after successful authentication."""
    ip = _get_client_ip()
    normalized_username = _normalize_identity(username)
    keys = []
    if ip:
        keys.extend([
            _auth_fail_key('ip', ip),
            _auth_lock_key('ip', ip),
        ])
    if normalized_username:
        keys.extend([
            _auth_fail_key('user', normalized_username),
            _auth_lock_key('user', normalized_username),
        ])
    for key in keys:
        _delete_key(key)


def consume_action_quota(
    action_key: str,
    *,
    limit: int,
    window_seconds: int,
    subject: str | None = None,
    include_ip: bool = True,
) -> tuple[bool, int]:
    """Consume a short-lived quota bucket for sensitive actions.

    Returns ``(allowed, retry_after_seconds)``.
    """
    if limit <= 0 or window_seconds <= 0:
        return True, 0

    bucket = int(time.time()) // max(1, window_seconds)
    retry_after = max(1, window_seconds - (int(time.time()) % max(1, window_seconds)))
    normalized_subject = _normalize_identity(subject)
    counters = []

    if include_ip:
        ip = _get_client_ip()
        if ip:
            counters.append(('ip', ip))
    if normalized_subject:
        counters.append(('subject', normalized_subject))

    if not counters:
        counters.append(('global', action_key))

    for kind, value in counters:
        key = f'action:quota:{action_key}:{kind}:{value}:{bucket}'
        if _incr_with_ttl(key, window_seconds) > limit:
            log_security_event(
                'action_quota_exceeded',
                'Sensitive action quota exceeded',
                action=action_key,
                scope=kind,
                subject=normalized_subject,
            )
            return False, retry_after

    return True, 0


def is_safe_redirect_target(target: str | None) -> bool:
    """Allow only relative URLs or same-origin redirects."""
    if not target:
        return False

    candidate = target.strip()
    if not candidate:
        return False
    if candidate.startswith('//'):
        return False

    test_url = urlsplit(urljoin(request.host_url, candidate))
    host_url = urlsplit(request.host_url)
    if test_url.scheme not in {'http', 'https'}:
        return False
    return test_url.netloc == host_url.netloc


def get_safe_redirect_target(target: str | None, fallback_endpoint: str = 'missions.index', **values) -> str:
    """Return a validated redirect target or a trusted fallback URL."""
    if is_safe_redirect_target(target):
        return target.strip()
    return url_for(fallback_endpoint, **values)


def rotate_session_identifier(*, clear_session: bool = False) -> None:
    """Rotate server-side session identifiers when available."""
    if clear_session:
        session.clear()

    regenerate = getattr(current_app.session_interface, 'regenerate', None)
    if callable(regenerate):
        try:
            regenerate(session)
        except Exception as exc:
            current_app.logger.warning(f'Session regeneration failed: {exc}')
    session.modified = True


def clear_auth_cookies(response):
    """Delete authentication cookies on logout or account deletion."""
    response.delete_cookie(
        current_app.config.get('SESSION_COOKIE_NAME', 'session'),
        path='/',
        secure=bool(current_app.config.get('SESSION_COOKIE_SECURE')),
        httponly=True,
        samesite=current_app.config.get('SESSION_COOKIE_SAMESITE', 'Lax'),
    )
    response.delete_cookie(
        current_app.config.get('REMEMBER_COOKIE_NAME', 'remember_token'),
        path='/',
        secure=bool(current_app.config.get('REMEMBER_COOKIE_SECURE')),
        httponly=True,
        samesite=current_app.config.get('REMEMBER_COOKIE_SAMESITE', 'Lax'),
    )
    return response
