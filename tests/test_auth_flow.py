from unittest.mock import patch

from app.datetime_utils import utc_now
from app.extensions import db
from app.models import EmailOTP, User
from app.services.otp_service import OTPService
from tests.test_support import AppTestCase


class AuthFlowTests(AppTestCase):
    def test_signup_rejects_short_password(self):
        response = self.client.post(
            '/signup',
            data={
                'username': 'new_user',
                'password': '12345',
                'confirm_password': '12345',
                'email': 'new@example.com',
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Password must be at least 6 characters', response.data)
        self.assertIsNone(User.query.filter_by(username='new_user').first())

    def test_signup_creates_account_with_valid_data(self):
        with patch.object(OTPService, 'generate_code', return_value='999888'):
            response = self.client.post(
                '/signup',
                data={
                    'username': 'valid_user',
                    'password': '123456',
                    'confirm_password': '123456',
                    'email': 'valid@example.com',
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn('/verify-email', response.headers['Location'])
        self.assertIsNotNone(User.query.filter_by(username='valid_user').first())

        otp_row = EmailOTP.query.filter_by(email='valid@example.com', purpose=OTPService.PURPOSE_EMAIL_VERIFY).order_by(EmailOTP.created_at.desc()).first()
        self.assertIsNotNone(otp_row)

        verify_response = self.client.post(
            '/verify-email',
            data={'otp_code': '999888'},
            follow_redirects=False,
        )
        self.assertEqual(verify_response.status_code, 302)
        self.assertIn('/login', verify_response.headers['Location'])

        created_user = User.query.filter_by(username='valid_user').first()
        db.session.expire_all()
        refreshed_user = db.session.get(User, created_user.id)
        self.assertIsNotNone(refreshed_user.email_verified_at)

    def test_signup_and_email_verify_support_json(self):
        with patch.object(OTPService, 'generate_code', return_value='121212'):
            signup_response = self.client.post(
                '/signup',
                json={
                    'username': 'json_user',
                    'password': 'Newpass1!',
                    'confirm_password': 'Newpass1!',
                    'email': 'json_user@example.com',
                },
            )

        self.assertEqual(signup_response.status_code, 201)
        signup_payload = signup_response.get_json()
        self.assertEqual(signup_payload['status'], 201)
        self.assertTrue(signup_payload['data']['requires_email_verification'])

        verify_response = self.client.post('/verify-email', json={'otp_code': '121212'})
        self.assertEqual(verify_response.status_code, 200)
        verify_payload = verify_response.get_json()
        self.assertEqual(verify_payload['status'], 200)
        self.assertTrue(verify_payload['data']['verified'])

    def test_login_with_invalid_password_is_rejected(self):
        self.create_user(username='login_user', password='abcdef')
        response = self.client.post(
            '/login',
            data={'username': 'login_user', 'password': 'wrongpass'},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Invalid username or password', response.data)

    def test_login_with_email_identifier_uses_otp(self):
        user = self.create_user(username='otp_user', password='abcdef', email='otp_user@example.com')
        user.email_verified_at = utc_now()
        db.session.commit()

        with patch.object(OTPService, 'generate_code', return_value='123456'):
            response = self.client.post(
                '/login',
                data={'username': 'otp_user@example.com', 'password': 'abcdef'},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/otp', response.headers['Location'])

        otp_row = EmailOTP.query.filter_by(email=user.email, purpose=OTPService.PURPOSE_LOGIN).order_by(EmailOTP.created_at.desc()).first()
        self.assertIsNotNone(otp_row)

        otp_response = self.client.post(
            '/login/otp',
            data={'otp_code': '123456'},
            follow_redirects=False,
        )
        self.assertEqual(otp_response.status_code, 302)
        self.assertIn('/missions', otp_response.headers['Location'])

    def test_login_with_unverified_email_requires_email_verification(self):
        self.create_user(username='pending_user', password='abcdef', email='pending@example.com')

        with patch.object(OTPService, 'generate_code', return_value='222333'):
            response = self.client.post(
                '/login',
                data={'username': 'pending_user', 'password': 'abcdef'},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn('/verify-email', response.headers['Location'])

    def test_forgot_password_and_reset_support_json(self):
        user = self.create_user(username='reset_user', password='abcdef', email='reset_user@example.com')

        with patch.object(OTPService, 'generate_code', return_value='654321'):
            forgot_response = self.client.post(
                '/forgot-password',
                json={'identifier': user.email},
            )

        self.assertEqual(forgot_response.status_code, 200)
        self.assertTrue(forgot_response.get_json()['ok'])

        otp_row = EmailOTP.query.filter_by(email=user.email, purpose=OTPService.PURPOSE_PASSWORD_RESET).order_by(EmailOTP.created_at.desc()).first()
        self.assertIsNotNone(otp_row)

        reset_response = self.client.post(
            '/reset-password',
            json={
                'identifier': user.email,
                'otp_code': '654321',
                'password': 'Newpass1!',
                'confirm_password': 'Newpass1!',
            },
        )
        self.assertEqual(reset_response.status_code, 200)
        self.assertTrue(reset_response.get_json()['ok'])

        db.session.expire_all()
        refreshed_user = db.session.get(User, user.id)
        self.assertTrue(refreshed_user.check_password('Newpass1!'))

    def test_login_json_contract_includes_status_and_data(self):
        user = self.create_user(username='contract_user', password='abcdef', email='contract_user@example.com')
        user.email_verified_at = utc_now()
        db.session.commit()

        with patch.object(OTPService, 'generate_code', return_value='333444'):
            response = self.client.post(
                '/login',
                json={'identifier': user.email, 'password': 'abcdef'},
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['status'], 200)
        self.assertTrue(payload['data']['requires_otp'])
        self.assertIn('next_url', payload['data'])
