"""Temporary script to verify seller flag and product creation."""
import os, sys
# ensure project root is on python path even with spaces
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import create_app
from app.extensions import db
from app.models import User, Product

app = create_app('development')
with app.app_context():
    u = User.query.filter_by(username='testuser').first()
    if not u:
        u = User(username='testuser', email='test@example.com')
        u.set_password('password')
        db.session.add(u)
        db.session.commit()
    u.is_seller = True
    db.session.commit()
    print('user flags', u.is_seller, u.can_sell)

    p = Product(name='test prod', price=50, seller_id=u.id)
    db.session.add(p)
    db.session.commit()
    print('product created', p.id, 'seller_id', p.seller_id)
