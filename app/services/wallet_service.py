"""
Wallet balance and transaction safety helpers.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.extensions import db
from app.datetime_utils import utc_now
from app.models import User, WalletTransaction, WithdrawRequest
from app.validators import ValidationError, validate_positive_int


class WalletService:
    """Encapsulates wallet mutations so routes stay small and safe."""

    @staticmethod
    def _lock_user(user_id: int) -> User:
        user = db.session.execute(
            select(User).where(User.id == user_id).with_for_update()
        ).scalar_one_or_none()
        if not user:
            raise ValidationError("User not found")
        return user

    @staticmethod
    def record_transaction(*, user_id: int, amount: int, transaction_type: str, status: str = "completed",
                           balance_before: float | None = None, balance_after: float | None = None,
                           reference_type: str | None = None, reference_id: int | None = None,
                           details: str | None = None) -> WalletTransaction:
        transaction = WalletTransaction(
            user_id=user_id,
            amount=amount,
            transaction_type=transaction_type,
            status=status,
            balance_before=balance_before,
            balance_after=balance_after,
            reference_type=reference_type,
            reference_id=reference_id,
            details=details,
        )
        db.session.add(transaction)
        return transaction

    @staticmethod
    def debit_user(*, user_id: int, amount: int, transaction_type: str, reference_type: str | None = None,
                   reference_id: int | None = None, details: str | None = None, status: str = "completed",
                   commit: bool = False) -> User:
        amount = validate_positive_int(amount, "Amount")
        user = WalletService._lock_user(user_id)
        balance_before = float(user.coins or 0)
        if balance_before < amount:
            raise ValidationError("Insufficient TNNO")

        user.coins = balance_before - amount
        WalletService.record_transaction(
            user_id=user_id,
            amount=-amount,
            transaction_type=transaction_type,
            status=status,
            balance_before=balance_before,
            balance_after=float(user.coins or 0),
            reference_type=reference_type,
            reference_id=reference_id,
            details=details,
        )
        if commit:
            db.session.commit()
        return user

    @staticmethod
    def credit_user(*, user_id: int, amount: int, transaction_type: str, reference_type: str | None = None,
                    reference_id: int | None = None, details: str | None = None, status: str = "completed",
                    commit: bool = False) -> User:
        amount = validate_positive_int(amount, "Amount")
        user = WalletService._lock_user(user_id)
        balance_before = float(user.coins or 0)
        user.coins = balance_before + amount
        WalletService.record_transaction(
            user_id=user_id,
            amount=amount,
            transaction_type=transaction_type,
            status=status,
            balance_before=balance_before,
            balance_after=float(user.coins or 0),
            reference_type=reference_type,
            reference_id=reference_id,
            details=details,
        )
        if commit:
            db.session.commit()
        return user

    @staticmethod
    def create_withdrawal(*, user_id: int, amount: int, wallet: str, name: str, network: str | None = None) -> WithdrawRequest:
        amount = validate_positive_int(amount, "Amount")
        if not wallet:
            raise ValidationError("Address or phone number is required")
        if not name:
            raise ValidationError("Name is required")
        if network and network not in {"ERC20", "BEP20", "TRC20", "PHONE"}:
            raise ValidationError("Invalid payout method")

        user = WalletService._lock_user(user_id)
        balance_before = float(user.coins or 0)
        if balance_before < amount:
            raise ValidationError("Insufficient TNNO")

        user.coins = balance_before - amount
        withdraw = WithdrawRequest(
            user_id=user_id,
            amount=amount,
            wallet=wallet,
            name=name,
            status="pending",
            created_at=utc_now(),
        )
        db.session.add(withdraw)
        db.session.flush()
        WalletService.record_transaction(
            user_id=user_id,
            amount=-amount,
            transaction_type="withdraw_request",
            status="pending",
            balance_before=balance_before,
            balance_after=float(user.coins or 0),
            reference_type="withdraw_request",
            reference_id=withdraw.id,
            details=network or None,
        )
        db.session.commit()
        return withdraw
