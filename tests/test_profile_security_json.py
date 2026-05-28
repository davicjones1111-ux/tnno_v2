from unittest.mock import patch

from app.extensions import db
from app.models import EmailOTP, User
from app.services.otp_service import OTPService
from tests.test_support import AppTestCase


class ProfileSecurityJsonTests(AppTestCase):
    def _log_in_directly(self, user: User) -> None:
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user.id)
            session['_fresh'] = True

    def test_email_verification_json_flow(self):
        user = self.create_user(username='verify_user', password='abcdef', email='verify_user@example.com')
        self._log_in_directly(user)

        with patch.object(OTPService, 'generate_code', return_value='111222'):
            send_response = self.client.post('/profile/settings/security/email/send', json={})

        self.assertEqual(send_response.status_code, 200)
        self.assertTrue(send_response.get_json()['ok'])

        otp_row = EmailOTP.query.filter_by(email=user.email, purpose=OTPService.PURPOSE_EMAIL_VERIFY).order_by(EmailOTP.created_at.desc()).first()
        self.assertIsNotNone(otp_row)

        verify_response = self.client.post('/profile/settings/security/email/verify', json={'otp_code': '111222'})
        self.assertEqual(verify_response.status_code, 200)
        self.assertTrue(verify_response.get_json()['ok'])

        db.session.expire_all()
        refreshed_user = db.session.get(User, user.id)
        self.assertIsNotNone(refreshed_user.email_verified_at)
