from app import create_app
from app.models import BlockchainState

app = create_app('development')
with app.app_context():
    rows = BlockchainState.query.all()
    print('=== state contents ===')
    if not rows:
        print('no rows')
    for r in rows:
        print('ROW', r.coin_type, r.last_block)
    print('=== end state ===')