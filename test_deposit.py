"""Quick script to simulate deposit scanning"""
from app import create_app
from app.extensions import db
from app.models import User, Deposit
from app.services.blockchain_service import BlockchainChecker, TransferRecord
from datetime import datetime, timedelta

app = create_app('development')
app.app_context().push()

# ensure tables
db.create_all()

# create test user
user = User.query.filter_by(username='testuser').first()
if not user:
    user = User(username='testuser', email='test@example.com')
    user.set_password('password')
    db.session.add(user)
    db.session.commit()

# create deposit
deposit = DepositService = None
from app.services.deposit_service import DepositService

deposit = DepositService.create_deposit(user.id, '10', 'USDT')
print('Created deposit', deposit.id, deposit.expected_amount, 'scan_from', deposit.scan_from_block, 'last_scanned', deposit.last_scanned_block)
# modify scan bounds so fake log will be considered
deposit.scan_from_block = 0
deposit.last_scanned_block = 0
from app.extensions import db
# ensure changes saved
db.session.commit()

# fake blockchain service by monkeypatching
checker = BlockchainChecker(app)

# create fake transfer matching
fake_transfer = TransferRecord(tx_hash='0xabc', block_number=5, amount=deposit.expected_amount, coin_type='USDT')

# monkeypatch service methods
checker.service = checker.service or checker.service
checker.service = checker.service or checker.service

# override is_available and get_current_block
class FakeService:
    def is_available(self):
        return True
    def get_current_block(self):
        return 105
    def get_transfer_logs_to_wallet(self, coin_type, from_block, to_block):
        return [fake_transfer]
    def get_block_timestamp(self, block_number, cache):
        return datetime.utcnow()

checker.service = FakeService()

# run check
checker._check_pending_deposits()
print('Deposit status after check:', Deposit.query.get(deposit.id).status)
print('User coins:', user.coins)
