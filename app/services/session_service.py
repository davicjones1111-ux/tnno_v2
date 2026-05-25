"""
Tracked session and authentication event helpers.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from flask import current_app, request, session
from flask_login import current_user, logout_user
from sqlalchemy import inspect, text

from app.datetime_utils import utc_now
from app.extensions import db
from app.models import AdminAuditLog, AuthEvent, User, UserSession
from app.security import _get_client_ip, rotate_session_identifier
from app.services.email_service import EmailService


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _parse_user_agent(user_agent: str) -> tuple[str, str, str]:
    ua = (user_agent or '').lower()
    browser = 'Unknown Browser'
    operating_system = 'Unknown OS'
    device_type = 'Desktop'

    if 'edg/' in ua:
        browser = 'Edge'
    elif 'chrome/' in ua and 'chromium' not in ua:
        browser = 'Chrome'
    elif 'safari/' in ua and 'chrome/' not in ua:
        browser = 'Safari'
    elif 'firefox/' in ua:
        browser = 'Firefox'

    if 'windows' in ua:
        operating_system = 'Windows'
    elif 'iphone' in ua or 'ipad' in ua or 'ios' in ua:
        operating_system = 'iOS'
    elif 'android' in ua:
        operating_system = 'Android'
        device_type = 'Mobile'
    elif 'mac os x' in ua or 'macintosh' in ua:
        operating_system = 'macOS'
    elif 'linux' in ua:
        operating_system = 'Linux'

    if any(token in ua for token in ('iphone', 'android', 'mobile')):
        device_type = 'Mobile'
    elif 'ipad' in ua or 'tablet' in ua:
        device_type = 'Tablet'

    return browser, operating_system, device_type


def _location_hint() -> str:
    parts = [
        request.headers.get('X-Appengine-City'),
        request.headers.get('Fly-Region'),
        request.headers.get('CF-IPCountry'),
        request.headers.get('X-Render-Region'),
    ]
    return ', '.join(part for part in parts if part) or 'Unknown'


class SessionService:
    @staticmethod
    def ensure_security_schema() -> None:
        """Best-effort schema patching for auth/session/security tables and columns."""
        inspector = inspect(db.engine)
        table_names = set(inspector.get_table_names())
        engine_url = str(db.engine.url).lower()
        is_postgres = 'postgresql' in engine_url
        id_column = 'SERIAL PRIMARY KEY' if is_postgres else 'INTEGER PRIMARY KEY AUTOINCREMENT'

        alter_statements = []

        if 'users' in table_names:
            user_columns = {col['name'] for col in inspector.get_columns('users')}
            if 'email_verified_at' not in user_columns:
                alter_statements.append('ALTER TABLE users ADD COLUMN email_verified_at TIMESTAMP')
            if 'two_factor_enabled' not in user_columns:
                alter_statements.append('ALTER TABLE users ADD COLUMN two_factor_enabled BOOLEAN DEFAULT FALSE')
            if 'security_alerts_enabled' not in user_columns:
                alter_statements.append('ALTER TABLE users ADD COLUMN security_alerts_enabled BOOLEAN DEFAULT TRUE')
            if 'password_changed_at' not in user_columns:
                alter_statements.append('ALTER TABLE users ADD COLUMN password_changed_at TIMESTAMP')
            if 'last_login_at' not in user_columns:
                alter_statements.append('ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP')
            if 'last_login_ip' not in user_columns:
                alter_statements.append('ALTER TABLE users ADD COLUMN last_login_ip VARCHAR(64)')
            if 'last_login_user_agent' not in user_columns:
                alter_statements.append('ALTER TABLE users ADD COLUMN last_login_user_agent VARCHAR(255)')

        if 'user_sessions' not in table_names:
            alter_statements.append(
                'CREATE TABLE user_sessions ('
                f'id {id_column}, '
                'user_id INTEGER NOT NULL, '
                'token_hash VARCHAR(64) NOT NULL, '
                'session_label VARCHAR(120), '
                'ip_address VARCHAR(64), '
                'last_seen_ip VARCHAR(64), '
                'location_hint VARCHAR(120), '
                'user_agent VARCHAR(255), '
                'browser VARCHAR(80), '
                'operating_system VARCHAR(80), '
                'device_type VARCHAR(32), '
                'login_at TIMESTAMP, '
                'last_activity_at TIMESTAMP, '
                'expires_at TIMESTAMP NOT NULL, '
                'absolute_expires_at TIMESTAMP NOT NULL, '
                'revoked_at TIMESTAMP, '
                'created_at TIMESTAMP, '
                'updated_at TIMESTAMP, '
                'FOREIGN KEY(user_id) REFERENCES users (id)'
                ')'
            )

        if 'auth_events' not in table_names:
            alter_statements.append(
                'CREATE TABLE auth_events ('
                f'id {id_column}, '
                'user_id INTEGER, '
                'username VARCHAR(80), '
                'event_type VARCHAR(40) NOT NULL, '
                'status VARCHAR(20) NOT NULL DEFAULT \'info\', '
                'ip_address VARCHAR(64), '
                'location_hint VARCHAR(120), '
                'user_agent VARCHAR(255), '
                'browser VARCHAR(80), '
                'operating_system VARCHAR(80), '
                'details TEXT, '
                'created_at TIMESTAMP, '
                'FOREIGN KEY(user_id) REFERENCES users (id)'
                ')'
            )

        if 'email_otps' not in table_names:
            alter_statements.append(
                'CREATE TABLE email_otps ('
                f'id {id_column}, '
                'user_id INTEGER, '
                'email VARCHAR(120) NOT NULL, '
                'purpose VARCHAR(40) NOT NULL, '
                'otp_hash VARCHAR(64) NOT NULL, '
                'request_ip VARCHAR(64), '
                'attempt_count INTEGER DEFAULT 0, '
                'max_attempts INTEGER DEFAULT 5, '
                'expires_at TIMESTAMP NOT NULL, '
                'cooldown_until TIMESTAMP, '
                'consumed_at TIMESTAMP, '
                'created_at TIMESTAMP, '
                'FOREIGN KEY(user_id) REFERENCES users (id)'
                ')'
            )

        if 'password_history' not in table_names:
            alter_statements.append(
                'CREATE TABLE password_history ('
                f'id {id_column}, '
                'user_id INTEGER NOT NULL, '
                'password_hash VARCHAR(255) NOT NULL, '
                'created_at TIMESTAMP, '
                'FOREIGN KEY(user_id) REFERENCES users (id)'
                ')'
            )

        if 'admin_audit_logs' not in table_names:
            alter_statements.append(
                'CREATE TABLE admin_audit_logs ('
                f'id {id_column}, '
                'admin_user_id INTEGER NOT NULL, '
                'action VARCHAR(80) NOT NULL, '
                'target_type VARCHAR(80), '
                'target_id INTEGER, '
                'ip_address VARCHAR(64), '
                'details TEXT, '
                'created_at TIMESTAMP, '
                'FOREIGN KEY(admin_user_id) REFERENCES users (id)'
                ')'
            )

        for statement in alter_statements:
            db.session.execute(text(statement))

        if alter_statements:
            db.session.commit()

    @staticmethod
    def record_auth_event(
        event_type: str,
        *,
        user: User | None = None,
        username: str | None = None,
        status: str = 'info',
        details: str | None = None,
    ) -> AuthEvent:
        browser, operating_system, _device_type = _parse_user_agent(request.user_agent.string or '')
        row = AuthEvent(
            user_id=user.id if user else None,
            username=(username or (user.username if user else '') or '').strip() or None,
            event_type=event_type,
            status=status,
            ip_address=_get_client_ip(),
            location_hint=_location_hint(),
            user_agent=(request.user_agent.string or '')[:255],
            browser=browser,
            operating_system=operating_system,
            details=details,
        )
        db.session.add(row)
        db.session.flush()
        return row

    @staticmethod
    def create_authenticated_session(user: User, *, remember: bool = False) -> UserSession:
        now = utc_now()
        browser, operating_system, device_type = _parse_user_agent(request.user_agent.string or '')
        ip_address = _get_client_ip()
        location_hint = _location_hint()
        token = secrets.token_urlsafe(32)
        token_hash = _hash_token(token)

        inactivity_timeout = timedelta(minutes=int(current_app.config.get('SESSION_INACTIVITY_TIMEOUT_MINUTES', 720)))
        absolute_days = int(current_app.config.get('SESSION_ABSOLUTE_TIMEOUT_DAYS', 30))
        if remember:
            remember_duration = current_app.config.get('REMEMBER_COOKIE_DURATION')
            absolute_expires_at = now + (remember_duration if remember_duration else timedelta(days=absolute_days))
        else:
            absolute_expires_at = now + timedelta(days=min(absolute_days, 7))

        is_new_device = not UserSession.query.filter_by(
            user_id=user.id,
            browser=browser,
            operating_system=operating_system,
            device_type=device_type,
        ).first()

        row = UserSession(
            user_id=user.id,
            token_hash=token_hash,
            session_label=f'{browser} on {operating_system}',
            ip_address=ip_address,
            last_seen_ip=ip_address,
            location_hint=location_hint,
            user_agent=(request.user_agent.string or '')[:255],
            browser=browser,
            operating_system=operating_system,
            device_type=device_type,
            login_at=now,
            last_activity_at=now,
            expires_at=min(now + inactivity_timeout, absolute_expires_at),
            absolute_expires_at=absolute_expires_at,
        )
        db.session.add(row)

        user.last_login_at = now
        user.last_login_ip = ip_address
        user.last_login_user_agent = (request.user_agent.string or '')[:255]

        SessionService.record_auth_event(
            'login_success',
            user=user,
            status='success',
            details='new_device' if is_new_device else 'known_device',
        )
        db.session.commit()

        session['auth_session_token'] = token
        session['auth_session_touched_at'] = now.isoformat()
        session['auth_session_id'] = row.id

        if is_new_device and user.email and user.email_verified_at and user.security_alerts_enabled:
            EmailService.send_login_alert_email(
                user.email,
                browser=browser,
                operating_system=operating_system,
                ip_address=ip_address or 'Unknown',
                location_hint=location_hint,
                occurred_at=now,
            )
        return row

    @staticmethod
    def get_current_session() -> UserSession | None:
        token = session.get('auth_session_token')
        if not token:
            return None
        return UserSession.query.filter_by(token_hash=_hash_token(token)).first()

    @staticmethod
    def enforce_current_session() -> UserSession | None:
        if not current_user.is_authenticated:
            return None
        tracked = SessionService.get_current_session()
        if not tracked or tracked.user_id != current_user.id or not tracked.is_active:
            logout_user()
            rotate_session_identifier(clear_session=True)
            return None

        now = utc_now()
        grace_seconds = int(current_app.config.get('SESSION_ACTIVITY_GRACE_SECONDS', 60))
        last_touch_raw = session.get('auth_session_touched_at')
        if last_touch_raw:
            try:
                last_touch = max(now - timedelta(days=3650), datetime.fromisoformat(last_touch_raw))
            except Exception:
                last_touch = None
        else:
            last_touch = None

        if not last_touch or (now - last_touch).total_seconds() >= grace_seconds:
            tracked.last_activity_at = now
            tracked.last_seen_ip = _get_client_ip()
            tracked.expires_at = min(
                now + timedelta(minutes=int(current_app.config.get('SESSION_INACTIVITY_TIMEOUT_MINUTES', 720))),
                tracked.absolute_expires_at,
            )
            session['auth_session_touched_at'] = now.isoformat()
            db.session.commit()
        return tracked

    @staticmethod
    def revoke_current_session(*, reason: str = 'logout') -> None:
        tracked = SessionService.get_current_session()
        if tracked and tracked.revoked_at is None:
            tracked.revoked_at = utc_now()
            SessionService.record_auth_event(reason, user=current_user if current_user.is_authenticated else None, status='success')
            db.session.commit()
        session.pop('auth_session_token', None)
        session.pop('auth_session_touched_at', None)
        session.pop('auth_session_id', None)

    @staticmethod
    def revoke_user_session(user: User, session_id: int) -> bool:
        tracked = UserSession.query.filter_by(id=session_id, user_id=user.id).first()
        if not tracked or tracked.revoked_at is not None:
            return False
        tracked.revoked_at = utc_now()
        SessionService.record_auth_event('session_revoked', user=user, status='warning', details=f'session_id={session_id}')
        db.session.commit()
        return True

    @staticmethod
    def revoke_other_sessions(user: User) -> int:
        return SessionService.revoke_all_user_sessions(user, exclude_current=True)

    @staticmethod
    def revoke_all_user_sessions(user: User, *, exclude_current: bool = False) -> int:
        current_token = session.get('auth_session_token')
        current_hash = _hash_token(current_token) if current_token else ''
        query = UserSession.query.filter(
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
        )
        if exclude_current and current_hash:
            query = query.filter(UserSession.token_hash != current_hash)
        rows = query.all()
        now = utc_now()
        for row in rows:
            row.revoked_at = now
        if rows:
            SessionService.record_auth_event(
                'other_sessions_revoked' if exclude_current else 'all_sessions_revoked',
                user=user,
                status='warning',
                details=f'count={len(rows)}',
            )
            db.session.commit()
        return len(rows)

    @staticmethod
    def list_user_sessions(user: User, *, limit: int = 20) -> list[UserSession]:
        return UserSession.query.filter_by(user_id=user.id).order_by(UserSession.last_activity_at.desc()).limit(limit).all()

    @staticmethod
    def list_recent_auth_events(user: User, *, limit: int = 20) -> list[AuthEvent]:
        return AuthEvent.query.filter_by(user_id=user.id).order_by(AuthEvent.created_at.desc()).limit(limit).all()

    @staticmethod
    def cleanup_security_records() -> None:
        now = utc_now()
        auth_retention = timedelta(days=int(current_app.config.get('AUTH_EVENT_RETENTION_DAYS', 60)))
        session_retention = timedelta(days=int(current_app.config.get('SESSION_RETENTION_DAYS', 45)))
        UserSession.query.filter(
            (
                UserSession.revoked_at.isnot(None)
            ) | (
                UserSession.absolute_expires_at < now - session_retention
            ),
            UserSession.updated_at < now - session_retention,
        ).delete(synchronize_session=False)
        AuthEvent.query.filter(AuthEvent.created_at < now - auth_retention).delete(synchronize_session=False)
        db.session.commit()

    @staticmethod
    def log_admin_action(*, admin_user: User, action: str, target_type: str | None = None, target_id: int | None = None, details: str | None = None) -> None:
        row = AdminAuditLog(
            admin_user_id=admin_user.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip_address=_get_client_ip(),
            details=details,
        )
        db.session.add(row)
        db.session.commit()
