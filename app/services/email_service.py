"""
Resend-backed transactional email helpers.
"""
from __future__ import annotations

from datetime import datetime

import requests
from flask import current_app


class EmailService:
    RESEND_TESTING_FROM = 'onboarding@resend.dev'

    @staticmethod
    def is_configured() -> bool:
        return bool(current_app.config.get('RESEND_API_KEY'))

    @staticmethod
    def send_email(to_email: str, subject: str, *, html: str, text: str | None = None) -> bool:
        api_key = current_app.config.get('RESEND_API_KEY')
        if not api_key or not to_email:
            current_app.logger.warning(
                'Email sending skipped because RESEND_API_KEY or recipient is missing (to=%s, configured=%s)',
                to_email,
                bool(api_key),
            )
            return False

        payload = {
            'from': EmailService.RESEND_TESTING_FROM,
            'to': [to_email],
            'subject': subject,
            'html': html,
        }
        if text:
            payload['text'] = text
        reply_to = current_app.config.get('RESEND_REPLY_TO')
        if reply_to:
            payload['reply_to'] = reply_to

        try:
            response = requests.post(
                current_app.config.get('RESEND_API_BASE_URL'),
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                },
                json=payload,
                timeout=10,
            )
        except Exception as exc:
            current_app.logger.warning('Failed to reach Resend API: %s', exc)
            return False

        if response.ok:
            return True

        response_preview = (response.text or '')[:500]
        resend_error = response_preview
        try:
            error_json = response.json()
            if isinstance(error_json, dict):
                resend_error = error_json.get('message') or error_json.get('error') or response_preview
        except Exception:
            pass
        current_app.logger.warning(
            'Resend rejected email send status=%s to=%s from=%s error=%r',
            response.status_code,
            to_email,
            payload.get('from'),
            resend_error,
        )
        return False

    @staticmethod
    def send_otp_email(to_email: str, otp_code: str, *, purpose_label: str, expires_minutes: int) -> bool:
        subject = f'TNNO security code for {purpose_label}'
        html = (
            f'<div>'
            f'<h2>TNNO Security Code</h2>'
            f'<p>Your one-time code for {purpose_label} is:</p>'
            f'<p style="font-size:28px;font-weight:700;letter-spacing:4px;">{otp_code}</p>'
            f'<p>This code expires in {expires_minutes} minutes.</p>'
            f'</div>'
        )
        text = f'Your TNNO security code for {purpose_label} is {otp_code}. It expires in {expires_minutes} minutes.'
        return EmailService.send_email(to_email, subject, html=html, text=text)

    @staticmethod
    def send_verification_email(to_email: str, otp_code: str, *, expires_minutes: int) -> bool:
        return EmailService.send_otp_email(
            to_email,
            otp_code,
            purpose_label='email verification',
            expires_minutes=expires_minutes,
        )

    @staticmethod
    def send_password_reset_email(to_email: str, otp_code: str, *, expires_minutes: int) -> bool:
        return EmailService.send_otp_email(
            to_email,
            otp_code,
            purpose_label='password reset',
            expires_minutes=expires_minutes,
        )

    @staticmethod
    def send_login_alert_email(to_email: str, *, browser: str, operating_system: str, ip_address: str, location_hint: str, occurred_at: datetime) -> bool:
        timestamp = occurred_at.strftime('%Y-%m-%d %H:%M UTC') if occurred_at else 'Unknown time'
        subject = 'TNNO login alert: new device sign-in'
        html = (
            '<div>'
            '<h2>New TNNO sign-in detected</h2>'
            f'<p>Browser: <strong>{browser or "Unknown"}</strong></p>'
            f'<p>Operating system: <strong>{operating_system or "Unknown"}</strong></p>'
            f'<p>IP address: <strong>{ip_address or "Unknown"}</strong></p>'
            f'<p>Approximate location: <strong>{location_hint or "Unknown"}</strong></p>'
            f'<p>Time: <strong>{timestamp}</strong></p>'
            '<p>If this was not you, change your password and revoke other active sessions from Settings immediately.</p>'
            '</div>'
        )
        text = (
            f'New TNNO sign-in detected. Browser: {browser or "Unknown"}. '
            f'OS: {operating_system or "Unknown"}. IP: {ip_address or "Unknown"}. '
            f'Location: {location_hint or "Unknown"}. Time: {timestamp}.'
        )
        return EmailService.send_email(to_email, subject, html=html, text=text)
