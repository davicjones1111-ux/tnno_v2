import os
import tempfile
import unittest

from app import create_app
from app.extensions import db
from app.models import User


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.sqlite')
        os.environ['DATABASE_URL'] = f"sqlite:///{self.db_path}"
        self.app = create_app('testing')
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{self.db_path}",
            CSRF_ENABLED=False,
            WTF_CSRF_ENABLED=False,
        )
        self.app.jinja_env.globals.setdefault('csrf_token', lambda: '')
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()
        os.close(self.db_fd)
        os.unlink(self.db_path)
        os.environ.pop('DATABASE_URL', None)

    def create_user(self, username='tester', password='secret12', coins=0, **extra):
        user = User(username=username, coins=coins, **extra)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user

    def login(self, username='tester', password='secret12', follow_redirects=False):
        return self.client.post(
            '/login',
            data={'username': username, 'password': password, 'remember': 'true'},
            follow_redirects=follow_redirects,
        )
