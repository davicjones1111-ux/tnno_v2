"""Script to verify seller-related UI elements via Flask test client."""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.extensions import db
from app.models import User

app = create_app('development')

with app.app_context():
    db.create_all()
    # ensure test user
    user = User.query.filter_by(username='seller_test').first()
    if not user:
        user = User(username='seller_test', email='seller@test.com')
        user.set_password('password')
        db.session.add(user)
        db.session.commit()

    # not seller yet
    user.is_seller = False
    db.session.commit()

    client = app.test_client()
    # login helper
    # login route has no prefix
    resp = client.post('/login', data={'username': 'seller_test', 'password': 'password'}, follow_redirects=True)
    print('login status code', resp.status_code)
    assert b'Store' not in resp.data, 'Store link should not appear for non-seller'

    # make seller
    user.is_seller = True
    db.session.commit()
    resp = client.get('/', follow_redirects=True)
    assert b'Store' in resp.data, 'Store link should appear after seller flag'
    print('Store link appeared for seller as expected')

print('Seller UI test script completed')
