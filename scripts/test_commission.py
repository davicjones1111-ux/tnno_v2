"""Test commission system for sellers."""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.extensions import db
from app.models import User, Product, ProductFile

app = create_app('development')

with app.app_context():
    db.create_all()
    # ensure test user
    seller = User.query.filter_by(username='seller_test').first()
    if not seller:
        seller = User(username='seller_test', email='seller@test.com')
        seller.set_password('password')
        db.session.add(seller)
        db.session.commit()

    buyer = User.query.filter_by(username='buyer_test').first()
    if not buyer:
        buyer = User(username='buyer_test', email='buyer@test.com')
        buyer.set_password('password')
        buyer.coins = 100
        db.session.add(buyer)
        db.session.commit()

    seller.is_seller = True
    seller.seller_commission_rate = 0.03  # 3%
    db.session.commit()

    # create product
    product = Product(name='Commission Test', price=10, seller_id=seller.id)
    db.session.add(product)
    db.session.commit()

    # add file
    file = ProductFile(product_id=product.id, file_filename='test.zip', original_name='test.zip')
    db.session.add(file)
    db.session.commit()

    print(f'Initial seller coins: {seller.coins}')
    print(f'Initial buyer coins: {buyer.coins}')

    # simulate purchase
    client = app.test_client()
    resp = client.post('/login', data={'username': 'buyer_test', 'password': 'password'}, follow_redirects=True)
    print('buyer login status', resp.status_code)

    # buy product
    resp = client.post(f'/store/buy/{product.id}', data={'quantity': 1}, follow_redirects=True)
    print('buy status', resp.status_code)

    # check balances
    db.session.refresh(seller)
    db.session.refresh(buyer)
    print(f'After purchase - seller coins: {seller.coins} (expected +0.3)')
    print(f'After purchase - buyer coins: {buyer.coins} (expected -10)')

print('Commission test completed')