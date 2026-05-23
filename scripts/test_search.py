"""Test search functionality in merch store."""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.extensions import db
from app.models import User, Product

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

    user.is_seller = True
    db.session.commit()

    # create test products
    if not Product.query.filter_by(name='Test Widget').first():
        p1 = Product(name='Test Widget', price=10, seller_id=user.id)
        db.session.add(p1)
    if not Product.query.filter_by(name='Another Item').first():
        p2 = Product(name='Another Item', price=20, seller_id=user.id)
        db.session.add(p2)
    db.session.commit()

    client = app.test_client()
    resp = client.post('/login', data={'username': 'seller_test', 'password': 'password'}, follow_redirects=True)
    print('login status', resp.status_code)

    # test search
    resp = client.get('/store/?search=Widget')
    print('search for Widget:', 'Test Widget' in resp.data.decode())
    resp = client.get('/store/?search=Another')
    print('search for Another:', 'Another Item' in resp.data.decode())
    resp = client.get('/store/?search=Nonexistent')
    print('search for Nonexistent:', 'Test Widget' not in resp.data.decode() and 'Another Item' not in resp.data.decode())

print('Search test completed')