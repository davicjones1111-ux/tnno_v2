"""
Deposit Service
Business logic for NowPayments-backed deposits
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN

import requests
from flask import current_app, request, url_for
from sqlalchemy import inspect, text

from app.datetime_utils import utc_now
from app.extensions import db
from app.models import Deposit


AMOUNT_QUANT = Decimal('0.000001')
class DepositService:
    """Service for managing NowPayments-backed deposits."""

    @staticmethod
    def _amount_to_string(value: Decimal) -> str:
        return f'{value.quantize(AMOUNT_QUANT):f}'.rstrip('0').rstrip('.') or '0'

    @staticmethod
    def ensure_deposit_schema():
        """Best-effort schema patching for existing databases without migrations."""
        inspector = inspect(db.engine)
        if 'deposits' not in inspector.get_table_names():
            return

        existing_columns = {col['name'] for col in inspector.get_columns('deposits')}
        alter_statements = []

        if 'network' not in existing_columns:
            alter_statements.append("ALTER TABLE deposits ADD COLUMN network VARCHAR(20) NOT NULL DEFAULT 'TRC20'")
        if 'expected_amount' not in existing_columns:
            alter_statements.append('ALTER TABLE deposits ADD COLUMN expected_amount NUMERIC(24, 6)')
        if 'expires_at' not in existing_columns:
            alter_statements.append('ALTER TABLE deposits ADD COLUMN expires_at TIMESTAMP')
        if 'credited_at' not in existing_columns:
            alter_statements.append('ALTER TABLE deposits ADD COLUMN credited_at TIMESTAMP')
        if 'coin_type' not in existing_columns:
            alter_statements.append("ALTER TABLE deposits ADD COLUMN coin_type VARCHAR(20) DEFAULT 'USDT'")
        if 'amount' not in existing_columns:
            alter_statements.append('ALTER TABLE deposits ADD COLUMN amount FLOAT NOT NULL DEFAULT 0')
        if 'payment_id' not in existing_columns:
            alter_statements.append('ALTER TABLE deposits ADD COLUMN payment_id VARCHAR(255)')
        if 'coins_added' not in existing_columns:
            alter_statements.append('ALTER TABLE deposits ADD COLUMN coins_added INTEGER')
        # ensure the new coin_type column exists for multi-coin support
        # add seller flag to users if missing (shared patch location)
        user_cols = inspector.get_columns('users')
        user_col_names = {col['name'] for col in user_cols}
        if 'is_seller' not in user_col_names:
            alter_statements.append('ALTER TABLE users ADD COLUMN is_seller BOOLEAN DEFAULT 0')
        if 'seller_commission_rate' not in user_col_names:
            alter_statements.append('ALTER TABLE users ADD COLUMN seller_commission_rate NUMERIC(5,4) DEFAULT 0.03')

        # add seller_id to products if missing (enables per-user stores)
        if 'products' in inspector.get_table_names():
            prod_cols = {col['name'] for col in inspector.get_columns('products')}
            if 'seller_id' not in prod_cols:
                alter_statements.append('ALTER TABLE products ADD COLUMN seller_id INTEGER')
                alter_statements.append('CREATE INDEX IF NOT EXISTS ix_products_seller_id ON products (seller_id)')

        for statement in alter_statements:
            db.session.execute(text(statement))

        # Legacy status migration
        db.session.execute(text("UPDATE deposits SET status = 'success' WHERE status = 'completed'"))
        db.session.execute(text("UPDATE deposits SET status = 'expired' WHERE status = 'cancelled'"))

        # Indexes for provider-backed deposit lifecycle queries.
        db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_deposits_status_created ON deposits (status, created_at)'))
        db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_deposits_expected_amount ON deposits (expected_amount)'))
        db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_deposits_expires_at ON deposits (expires_at)'))
        db.session.commit()

        try:
            db.session.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS ux_deposits_payment_id ON deposits (payment_id)'))
            db.session.commit()
        except Exception:
            # If legacy duplicate data exists, keep runtime duplicate checks active.
            db.session.rollback()

    @staticmethod
    def _to_decimal(value) -> Decimal:
        try:
            dec = Decimal(str(value)).quantize(AMOUNT_QUANT)
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError('Invalid deposit amount.')

        if dec <= 0:
            raise ValueError('Amount must be greater than 0.')

        return dec

    @staticmethod
    def get_user_deposits(user_id, status=None, page=None, per_page=20, include_archived=False):
        """Get user's deposits."""
        query = Deposit.query.filter_by(user_id=user_id)
        if not include_archived:
            query = query.filter(Deposit.is_archived.is_(False))
        if status:
            query = query.filter_by(status=status)
        query = query.order_by(Deposit.created_at.desc())
        if page is not None:
            return query.paginate(page=page, per_page=per_page, error_out=False)
        return query.all()

    @staticmethod
    def get_deposit_by_id(deposit_id):
        """Get deposit by ID."""
        return Deposit.query.get(deposit_id)

    @staticmethod
    def get_deposit_by_payment_id(payment_id):
        """Get deposit by payment_id."""
        if not payment_id:
            return None
        return Deposit.query.filter_by(payment_id=payment_id).first()

    @staticmethod
    def create_nowpayments_deposit(user_id, raw_amount, network):
        """Create a deposit via NowPayments and persist a pending record."""
        DepositService.ensure_deposit_schema()

        amount = DepositService._to_decimal(raw_amount)
        min_deposit = Decimal(str(current_app.config.get('MIN_DEPOSIT_USDT') or 5)).quantize(AMOUNT_QUANT)

        if amount < min_deposit:
            raise ValueError(f'Minimum USDT deposit is {min_deposit.normalize()}.')

        allowed_networks = {'ERC20', 'BEP20'}
        network = (network or '').strip().upper()
        if network not in allowed_networks:
            raise ValueError('Invalid network selected. Only ERC20 and BEP20 are supported.')

        # Map network to NowPayments currency code
        currency_map = {
            'TRC20': 'usdttrc20',
            'ERC20': 'usdterc20',
            'BEP20': 'usdtbsc',
        }
        pay_currency = currency_map[network]

        api_key = current_app.config.get('NOWPAYMENTS_API_KEY')
        api_url = current_app.config.get('NOWPAYMENTS_API_URL')
        callback_url = current_app.config.get('NOWPAYMENTS_CALLBACK_URL')
        success_url = current_app.config.get('NOWPAYMENTS_SUCCESS_URL')
        cancel_url = current_app.config.get('NOWPAYMENTS_CANCEL_URL')

        if not api_key or not api_url:
            raise RuntimeError('NowPayments API is not configured. Check NOWPAYMENTS_API_KEY and NOWPAYMENTS_API_URL.')

        # Use explicit configuration when available. Fallback to the current host if no external URL is provided.
        if not callback_url:
            callback_url = url_for('nowpayments.webhook', _external=True)
        if not success_url:
            success_url = url_for('nowpayments.success', _external=True)
        if not cancel_url:
            cancel_url = url_for('deposit.index', _external=True)

        headers = {
            'x-api-key': api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

        payload = {
            'price_amount': DepositService._amount_to_string(amount),
            'price_currency': 'usd',
            'pay_currency': pay_currency,
            'order_id': f'deposit-{user_id}-{int(utc_now().timestamp())}',
            'order_description': f'RetroQuest USD deposit via {network}',
            'ipn_callback_url': callback_url,
            'success_url': success_url,
            'cancel_url': cancel_url,
        }

        try:
            current_app.logger.info(
                'Creating NowPayments invoice user_id=%s network=%s amount=%s api_url=%s',
                user_id,
                network,
                payload['price_amount'],
                api_url,
            )
            response = requests.post(api_url, json=payload, headers=headers, timeout=30)
        except requests.RequestException as exc:
            raise RuntimeError(f'NowPayments request failed: {exc}')

        response_text_preview = (response.text or '')[:1000]
        if not response.ok:
            current_app.logger.error(
                'NowPayments invoice HTTP error status=%s body=%s',
                response.status_code,
                response_text_preview,
            )
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = None

            if isinstance(error_payload, dict):
                error_code = error_payload.get('code') or response.status_code
                error_message = error_payload.get('message') or error_payload.get('error') or response.reason
                raise RuntimeError(f'NowPayments API error: {error_code} - {error_message}')

            raise RuntimeError(f'NowPayments API error: HTTP {response.status_code}.')

        try:
            data = response.json()
        except ValueError as exc:
            current_app.logger.error('NowPayments returned invalid JSON body=%s', response_text_preview)
            raise RuntimeError(f'NowPayments returned invalid JSON: {exc}')

        # Check if the API returned an error status
        if data.get('status') is False:
            error_code = data.get('code', 'UNKNOWN_ERROR')
            error_message = data.get('message', 'Unknown error from NowPayments API')
            raise RuntimeError(f'NowPayments API error: {error_code} - {error_message}')

        payment_id = (
            data.get('payment_id')
            or data.get('id')
            or data.get('token_id')
            or data.get('reference')
            or data.get('order_id')
        )
        payment_url = (
            data.get('payment_url')
            or data.get('invoice_url')
            or data.get('url')
            or data.get('checkout_url')
        )

        if not payment_id or not payment_url:
            current_app.logger.error(
                'NowPayments invoice response missing required fields keys=%s body=%s',
                sorted(data.keys()),
                response_text_preview,
            )
            raise RuntimeError('NowPayments API response missing payment_id or payment_url.')

        current_app.logger.info(
            'NowPayments invoice created user_id=%s payment_id=%s payment_url=%s',
            user_id,
            payment_id,
            payment_url,
        )

        usdt_amount = float(amount)
        expected_amount = None
        if data.get('pay_amount') is not None:
            expected_amount = Decimal(str(data.get('pay_amount')))
        else:
            expected_amount = Decimal(str(amount))

        if expected_amount is None:
            raise RuntimeError('Unable to determine expected deposit amount.')

        to_points = int(current_app.config.get('USDT_TO_POINTS') or 4000)
        points_amount = int((Decimal(str(usdt_amount)) * Decimal(to_points)).to_integral_value(rounding=ROUND_DOWN))

        if usdt_amount is None:
            raise RuntimeError('Invalid deposit amount before saving.')
        if points_amount is None:
            raise RuntimeError('Unable to calculate deposit points amount.')

        deposit = Deposit(
            user_id=user_id,
            amount=float(amount),
            usdt_amount=usdt_amount,
            expected_amount=expected_amount,
            points_amount=points_amount,
            network=network,
            payment_id=payment_id,
            status='pending',
            coin_type='USDT',
            created_at=utc_now(),
            expires_at=utc_now() + timedelta(seconds=int(current_app.config.get('DEPOSIT_TIMEOUT', 1200))),
        )
        db.session.add(deposit)
        db.session.commit()

        return deposit, payment_url

    @staticmethod
    def complete_deposit_payment(payment_id, incoming_status):
        """Complete a deposit when a payment provider confirms payment."""
        deposit = Deposit.query.filter_by(payment_id=payment_id).first()
        if not deposit:
            raise LookupError('Deposit not found.')

        status = incoming_status.strip().lower()
        if status not in ('confirmed', 'finished', 'partially_paid'):
            return deposit

        if deposit.status == 'completed':
            return deposit

        tnno_rate = int(current_app.config.get('USDT_TO_POINTS') or 4000)
        tnno_amount = int(float(deposit.amount) * tnno_rate)

        user = deposit.user
        user.coins = (user.coins or 0) + tnno_amount
        deposit.status = 'completed'
        deposit.coins_added = tnno_amount
        deposit.credited_at = utc_now()

        db.session.add(user)
        db.session.add(deposit)
        db.session.commit()
        return deposit

    @staticmethod
    def get_all_deposits(limit=100):
        """Get all deposits ordered by creation time."""
        return Deposit.query.order_by(Deposit.created_at.desc()).limit(limit).all()

    @staticmethod
    def get_deposit_stats():
        """Get deposit statistics."""
        total = Deposit.query.count()
        pending = Deposit.query.filter_by(status='pending').count()
        success = Deposit.query.filter_by(status='success').count()
        expired = Deposit.query.filter_by(status='expired').count()

        total_usdt = db.session.query(db.func.sum(Deposit.usdt_amount))\
            .filter(Deposit.status == 'success').scalar() or 0

        total_coins = db.session.query(db.func.sum(Deposit.coins_added))\
            .filter(Deposit.status == 'success').scalar() or 0

        return {
            'total': total,
            'pending': pending,
            'success': success,
            'expired': expired,
            'total_usdt': float(total_usdt),
            'total_coins': int(total_coins),
        }
