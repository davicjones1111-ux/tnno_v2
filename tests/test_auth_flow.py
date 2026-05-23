from app.models import User
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
        response = self.client.post(
            '/signup',
            data={
                'username': 'valid_user',
                'password': '123456',
                'confirm_password': '123456',
                'email': 'valid@example.com',
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(User.query.filter_by(username='valid_user').first())
        self.assertIn(b'Registration successful', response.data)

    def test_login_with_invalid_password_is_rejected(self):
        self.create_user(username='login_user', password='abcdef')
        response = self.client.post(
            '/login',
            data={'username': 'login_user', 'password': 'wrongpass'},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Invalid username or password', response.data)
