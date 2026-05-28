"""
Email OTP service for verification, 2FA, and password resets.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from flask import current_app

from app.datetime_utils import utc_now
from app.extensions import db
from app.models import EmailOTP, User
from app.security import _get_client_ip
from app.services.email_service import EmailService


class OTPService:
    PURPOSE_EMAIL_VERIFY = 'email_verify'
    PURPOSE_LOGIN = 'login_otp'
    PURPOSE_PASSWORD_RESET = 'password_reset'

    @staticmethod
    def _hash_code(code: str) -> str:
        secret = current_app.config.get('SECRET_KEY', '')
        return hashlib.sha256(f'{secret}:{code}'.encode('utf-8')).hexdigest()

    @staticmethod
    def _resolve_email(user: User | None = None, email: str | None = None) -> str:
        resolved = (email or (user.email if user else '') or '').strip().lower()
        if not resolved:
            raise ValueError('A verified email address is required for this action.')
        return resolved

    @staticmethod
    def generate_code() -> str:
        return f'{secrets.randbelow(1_000_000):06d}'

    @staticmethod
    def create_otp(*, user: User | None = None, email: str | None = None, purpose: str) -> str | None:
        target_email = OTPService._resolve_email(user, email)
        now = utc_now()
        cooldown_seconds = int(current_app.config.get('OTP_RESEND_COOLDOWN_SECONDS', 60))
        expires_minutes = int(current_app.config.get('OTP_EXPIRATION_MINUTES', 10))
        max_attempts = int(current_app.config.get('OTP_MAX_ATTEMPTS', 5))

        existing = EmailOTP.query.filter_by(
            email=target_email,
            purpose=purpose,
            consumed_at=None,
        ).order_by(EmailOTP.created_at.desc()).first()
        if existing and existing.cooldown_until and existing.cooldown_until > now:
            wait_seconds = int((existing.cooldown_until - now).total_seconds())
            raise ValueError(f'Please wait {max(wait_seconds, 1)} seconds before requesting another code.')

        EmailOTP.query.filter_by(email=target_email, purpose=purpose, consumed_at=None).update({
            'consumed_at': now
        })

        code = OTPService.generate_code()
        row = EmailOTP(
            user_id=user.id if user else None,
            email=target_email,
            purpose=purpose,
            otp_hash=OTPService._hash_code(code),
            request_ip=_get_client_ip(),
            max_attempts=max_attempts,
            expires_at=now + timedelta(minutes=expires_minutes),
            cooldown_until=now + timedelta(seconds=cooldown_seconds),
        )
        db.session.add(row)
        db.session.commit()

        if current_app.config.get('TESTING'):
            return code

        sent = False
        if purpose == OTPService.PURPOSE_EMAIL_VERIFY:
            sent = EmailService.send_verification_email(target_email, code, expires_minutes=expires_minutes)
        elif purpose == OTPService.PURPOSE_PASSWORD_RESET:
            sent = EmailService.send_password_reset_email(target_email, code, expires_minutes=expires_minutes)
        else:
            sent = EmailService.send_otp_email(target_email, code, purpose_label='login verification', expires_minutes=expires_minutes)

        if not sent:
            row.consumed_at = now
            db.session.commit()
            raise ValueError('Unable to send a security code right now. Please try again later.')
        return None

    @staticmethod
    def verify_otp(*, user: User | None = None, email: str | None = None, purpose: str, code: str) -> tuple[bool, str]:
        target_email = OTPService._resolve_email(user, email)
        now = utc_now()
        row = EmailOTP.query.filter_by(
            email=target_email,
            purpose=purpose,
            consumed_at=None,
        ).order_by(EmailOTP.created_at.desc()).first()
        if not row:
            return False, 'No security code was found. Please request a new one.'
        if row.expires_at <= now:
            row.consumed_at = now
            db.session.commit()
            return False, 'The security code has expired. Please request a new one.'
        if row.attempt_count >= row.max_attempts:
            row.consumed_at = now
            db.session.commit()
            return False, 'Too many incorrect attempts. Request a new code.'

        expected = OTPService._hash_code(code or '')
        if expected != row.otp_hash:
            row.attempt_count += 1
            if row.attempt_count >= row.max_attempts:
                row.consumed_at = now
            db.session.commit()
            remaining = max(row.max_attempts - row.attempt_count, 0)
            if remaining <= 0:
                return False, 'Too many incorrect attempts. Request a new code.'
            return False, f'Invalid security code. You have {remaining} attempt{"s" if remaining != 1 else ""} left.'

        row.consumed_at = now
        db.session.commit()
        return True, 'Code verified.'

    @staticmethod
    def cleanup_expired() -> None:
        now = utc_now()
        retention = timedelta(hours=int(current_app.config.get('OTP_RETENTION_HOURS', 24)))
        EmailOTP.query.filter(EmailOTP.expires_at < now - retention).delete(synchronize_session=False)
        EmailOTP.query.filter(EmailOTP.consumed_at.isnot(None), EmailOTP.created_at < now - retention).delete(
            synchronize_session=False
        )
        db.session.commit()
