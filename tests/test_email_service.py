from unittest.mock import Mock, patch

from app.services.email_service import EmailService
from tests.test_support import AppTestCase


class EmailServiceTests(AppTestCase):
    def test_resend_sender_uses_onboarding_address(self):
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured['url'] = url
            captured['headers'] = headers
            captured['json'] = json
            response = Mock()
            response.ok = True
            response.status_code = 200
            response.text = '{"id":"email_123"}'
            response.json.return_value = {'id': 'email_123'}
            return response

        self.app.config['RESEND_API_KEY'] = 'test-resend-key'
        with patch('app.services.email_service.requests.post', side_effect=fake_post):
            sent = EmailService.send_email('user@example.com', 'Test subject', html='<p>Hi</p>', text='Hi')

        self.assertTrue(sent)
        self.assertEqual(captured['json']['from'], 'onboarding@resend.dev')
