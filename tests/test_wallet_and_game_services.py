from app.extensions import db
from app.models import WalletTransaction
from app.services.game_wallet_service import GameWalletService
from app.services.wallet_service import WalletService
from tests.test_support import AppTestCase


class WalletAndGameServiceTests(AppTestCase):
    def test_create_withdrawal_debits_balance_and_creates_ledger_entry(self):
        user = self.create_user(username='wallet_user', password='abcdef', coins=5000)
        withdraw = WalletService.create_withdrawal(
            user_id=user.id,
            amount=1200,
            wallet='0xabc123',
            name='Wallet User',
            network='BEP20',
        )
        db.session.refresh(user)
        self.assertEqual(int(user.coins), 3800)
        self.assertEqual(withdraw.amount, 1200)
        entry = WalletTransaction.query.filter_by(
            user_id=user.id,
            transaction_type='withdraw_request'
        ).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.amount, -1200)

    def test_game_wallet_service_debits_refunds_and_pays_out(self):
        user_a = self.create_user(username='alpha', password='abcdef', coins=10000)
        user_b = self.create_user(username='beta', password='abcdef', coins=10000)

        GameWalletService.debit_match_stakes(user_ids=[user_a.id, user_b.id], amount=1000, room_id='room1')
        db.session.commit()
        db.session.refresh(user_a)
        db.session.refresh(user_b)
        self.assertEqual(int(user_a.coins), 9000)
        self.assertEqual(int(user_b.coins), 9000)

        GameWalletService.refund_match_stakes(user_ids=[user_a.id, user_b.id], amount=1000, room_id='room1', reason='draw')
        db.session.commit()
        db.session.refresh(user_a)
        db.session.refresh(user_b)
        self.assertEqual(int(user_a.coins), 10000)
        self.assertEqual(int(user_b.coins), 10000)

        GameWalletService.debit_match_stakes(user_ids=[user_a.id, user_b.id], amount=1000, room_id='room2')
        payout = GameWalletService.payout_winner(
            winner_id=user_a.id,
            loser_id=user_b.id,
            bet_amount=1000,
            platform_fee_bps=250,
            room_id='room2',
        )
        db.session.commit()
        db.session.refresh(user_a)
        db.session.refresh(user_b)
        self.assertEqual(payout['fee_amount'], 25)
        self.assertEqual(int(user_a.coins), 10975)
        self.assertEqual(int(user_b.coins), 9000)
