import io
import os
import tempfile

from werkzeug.datastructures import FileStorage

from app.models import User
from app.utils import save_uploaded_file_any
from tests.test_support import AppTestCase


class SecurityHardeningTests(AppTestCase):
    def test_login_blocks_external_redirect_targets(self):
        self.create_user(username='safe_user', password='secret12')

        response = self.client.post(
            '/login?next=https://evil.example/phish',
            data={'username': 'safe_user', 'password': 'secret12'},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertNotIn('evil.example', response.headers.get('Location', ''))

    def test_delete_account_requires_password_and_confirmation(self):
        self.create_user(username='delete_me', password='secret12')
        self.login(username='delete_me', password='secret12')

        response = self.client.post(
            '/profile/delete-account',
            data={},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(User.query.filter_by(username='delete_me').first())
        self.assertIn(b'Type DELETE to confirm account deletion', response.data)

    def test_upload_helper_blocks_executable_content(self):
        with tempfile.TemporaryDirectory() as tempdir:
            self.app.config['UPLOAD_FOLDER'] = tempdir
            with self.app.app_context():
                storage = FileStorage(
                    stream=io.BytesIO(b'MZ' + b'\x00' * 32),
                    filename='invoice.txt',
                    content_type='text/plain',
                )
                with self.assertRaises(ValueError):
                    save_uploaded_file_any(storage, 'work', {'txt'})

    def test_upload_helper_uses_random_filename(self):
        with tempfile.TemporaryDirectory() as tempdir:
            self.app.config['UPLOAD_FOLDER'] = tempdir
            with self.app.app_context():
                storage = FileStorage(
                    stream=io.BytesIO(b'hello world\n'),
                    filename='notes.txt',
                    content_type='text/plain',
                )
                stored = save_uploaded_file_any(storage, 'work', {'txt'}, allow_remote=False)

                self.assertIsNotNone(stored)
                self.assertRegex(stored, r'^uploads/work/[a-f0-9]{32}\.txt$')
                self.assertTrue(os.path.exists(os.path.join(tempdir, 'work', os.path.basename(stored))))
