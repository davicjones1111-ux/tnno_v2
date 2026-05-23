"""
Wallet-safe helpers for multiplayer game stake handling.
"""
from __future__ import annotations

from sqlalchemy import select

from app.extensions import db
from app.models import User
from app.services.wallet_service import WalletService
from app.validators import ValidationError, validate_positive_int


class GameWalletService:
    """Encapsulates stake debit/refund/payout flows for PvP games."""

    @staticmethod
    def _lock_users(user_ids: list[int]) -> dict[int, User]:
        ids = sorted({int(user_id) for user_id in user_ids if user_id})
        rows = db.session.execute(
            select(User).where(User.id.in_(ids)).order_by(User.id).with_for_update()
        ).scalars().all()
        users = {user.id: user for user in rows}
        missing = [user_id for user_id in ids if user_id not in users]
        if missing:
            raise ValidationError("User not found")
        return users

    @staticmethod
    def ensure_can_cover(user_ids: list[int], amount: int):
        amount = validate_positive_int(amount, "Amount")
        users = GameWalletService._lock_users(user_ids)
        for user_id, user in users.items():
            if float(user.coins or 0) < amount:
                raise ValidationError(f"User {user_id} has insufficient TNNO")
        return users

    @staticmethod
    def debit_match_stakes(*, user_ids: list[int], amount: int, room_id: str | None = None, commit: bool = False):
        amount = validate_positive_int(amount, "Amount")
        users = GameWalletService.ensure_can_cover(user_ids, amount)
        for user_id in sorted(users):
            user = users[user_id]
            balance_before = float(user.coins or 0)
            user.coins = balance_before - amount
            WalletService.record_transaction(
                user_id=user_id,
                amount=-amount,
                transaction_type='game_match_stake',
                status='completed',
                balance_before=balance_before,
                balance_after=float(user.coins or 0),
                reference_type='game_room',
                reference_id=None,
                details=room_id or 'emperors_circle',
            )
        if commit:
            db.session.commit()
        return users

    @staticmethod
    def refund_match_stakes(*, user_ids: list[int], amount: int, room_id: str | None = None, reason: str = 'refund', commit: bool = False):
        amount = validate_positive_int(amount, "Amount")
        users = GameWalletService._lock_users(user_ids)
        for user_id in sorted(users):
            user = users[user_id]
            balance_before = float(user.coins or 0)
            user.coins = balance_before + amount
            WalletService.record_transaction(
                user_id=user_id,
                amount=amount,
                transaction_type='game_match_refund',
                status='completed',
                balance_before=balance_before,
                balance_after=float(user.coins or 0),
                reference_type='game_room',
                reference_id=None,
                details=f'{room_id or "emperors_circle"}:{reason}',
            )
        if commit:
            db.session.commit()
        return users

    @staticmethod
    def payout_winner(*, winner_id: int, loser_id: int, bet_amount: int, platform_fee_bps: int,
                      room_id: str | None = None, commit: bool = False):
        bet_amount = validate_positive_int(bet_amount, "Amount")
        users = GameWalletService._lock_users([winner_id, loser_id])
        winner = users[winner_id]
        fee_amount = (bet_amount * int(platform_fee_bps or 0)) // 10000
        payout = (bet_amount * 2) - fee_amount
        net_gain = bet_amount - fee_amount
        balance_before = float(winner.coins or 0)
        winner.coins = balance_before + payout
        WalletService.record_transaction(
            user_id=winner_id,
            amount=payout,
            transaction_type='game_match_payout',
            status='completed',
            balance_before=balance_before,
            balance_after=float(winner.coins or 0),
            reference_type='game_room',
            reference_id=None,
            details=room_id or 'emperors_circle',
        )
        if fee_amount > 0:
            WalletService.record_transaction(
                user_id=winner_id,
                amount=0,
                transaction_type='game_platform_fee_applied',
                status='completed',
                balance_before=float(winner.coins or 0),
                balance_after=float(winner.coins or 0),
                reference_type='game_room',
                reference_id=None,
                details=f'{room_id or "emperors_circle"}:fee={fee_amount}',
            )
        if commit:
            db.session.commit()
        return {
            'payout': payout,
            'fee_amount': fee_amount,
            'winner_net_gain': net_gain,
        }
