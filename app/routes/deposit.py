"""
Deposit Routes
NowPayments deposit handling
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import hashlib
import hmac
import json
from urllib.parse import urlsplit

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required, current_user

from app.datetime_utils import utc_now
from app.extensions import db
from app.services import DepositService
from app.services.history_service import HistoryService


deposit_bp = Blueprint('deposit', __name__)
nowpayments_bp = Blueprint('nowpayments', __name__)


def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal('0')


def _format_usdt(value) -> str:
    dec = _to_decimal(value)
    return f'{dec.quantize(Decimal("0.000001")):f}'.rstrip('0').rstrip('.')


def _normalize_network(network: str) -> str:
    return (network or '').strip().upper()


def _is_json_request() -> bool:
    accept_header = request.headers.get('Accept', '')
    return (
        request.is_json or
        'application/json' in accept_header.lower() or
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    )


def _json_error(message: str, status: int = 400):
    current_app.logger.error('Deposit route error: %s', message)
    return jsonify({'error': message}), status


def _is_allowed_payment_redirect(target: str) -> bool:
    parsed = urlsplit(target or '')
    if parsed.scheme != 'https' or not parsed.netloc:
        return False
    host = parsed.netloc.lower()
    allowed_hosts = current_app.config.get('NOWPAYMENTS_ALLOWED_HOSTS') or ('nowpayments.io',)
    return any(host == allowed or host.endswith(f'.{allowed}') for allowed in allowed_hosts)


@nowpayments_bp.route('/create-deposit', methods=['POST'])
@login_required
def create_deposit():
    """Create a new NowPayments deposit and redirect the user to the payment URL."""
    amount = (request.form.get('amount') or '').strip()
    network = _normalize_network(request.form.get('network') or '')

    if not amount:
        message = 'Missing deposit amount.'
        if _is_json_request():
            return _json_error(message, 400)
        flash(message, 'error')
        return redirect(url_for('deposit.index'))

    if not network:
        message = 'Missing deposit network selection.'
        if _is_json_request():
            return _json_error(message, 400)
        flash(message, 'error')
        return redirect(url_for('deposit.index'))

    try:
        deposit, payment_url = DepositService.create_nowpayments_deposit(
            user_id=current_user.id,
            raw_amount=amount,
            network=network,
        )
    except ValueError as exc:
        message = str(exc) or 'Invalid deposit request.'
        if _is_json_request():
            return _json_error(message, 400)
        flash(message, 'error')
        return redirect(url_for('deposit.index'))
    except RuntimeError as exc:
        message = str(exc) or 'Unable to create deposit.'
        if _is_json_request():
            return _json_error(message, 502)
        flash(message, 'error')
        return redirect(url_for('deposit.index'))
    except Exception as exc:
        current_app.logger.exception('Unexpected deposit creation failure')
        if _is_json_request():
            return _json_error('Internal server error while creating deposit.', 500)
        flash('Internal server error while creating deposit.', 'error')
        return redirect(url_for('deposit.index'))

    if _is_json_request():
        return jsonify({'status': 'success', 'payment_url': payment_url}), 200

    if not _is_allowed_payment_redirect(payment_url):
        current_app.logger.error('Blocked unexpected NowPayments redirect host: %s', payment_url)
        flash('Payment provider returned an invalid redirect URL.', 'error')
        return redirect(url_for('deposit.index'))

    return redirect(payment_url, code=303)


def _sort_object(obj):
    if isinstance(obj, dict):
        return {k: _sort_object(obj[k]) for k in sorted(obj)}
    if isinstance(obj, list):
        return [_sort_object(item) for item in obj]
    return obj


def _verify_nowpayments_signature(request, secret):
    signature = request.headers.get('X-NowPayments-Sig') or request.headers.get('x-nowpayments-sig')
    if not signature or not secret:
        return False

    body_bytes = request.get_data()
    try:
        payload = json.loads(body_bytes.decode('utf-8'))
    except Exception:
        return False

    sorted_json = json.dumps(_sort_object(payload), separators=(',', ':'), ensure_ascii=False)
    computed = hmac.new(secret.encode(), sorted_json.encode('utf-8'), hashlib.sha512).hexdigest()
    return hmac.compare_digest(computed, signature)


@nowpayments_bp.route('/webhook', methods=['POST'])
def webhook():
    """Handle NowPayments webhook callbacks to finalize deposit crediting."""
    secret = current_app.config.get('NOWPAYMENTS_IPN_SECRET')
    if secret:
        if not _verify_nowpayments_signature(request, secret):
            return jsonify({'error': 'Invalid signature.'}), 403

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({'error': 'Invalid JSON payload.'}), 400

    payment_id = (
        payload.get('payment_id')
        or payload.get('id')
        or payload.get('order_id')
        or payload.get('reference')
    )
    payment_status = (
        payload.get('payment_status')
        or payload.get('status')
        or ''
    ).strip().lower()

    if not payment_id:
        return jsonify({'error': 'Missing payment_id.'}), 400

    deposit = DepositService.get_deposit_by_payment_id(payment_id)
    if not deposit:
        return jsonify({'error': 'Deposit not found.'}), 404

    try:
        if payment_status in ('finished', 'partially_paid', 'confirmed'):
            DepositService.complete_deposit_payment(payment_id, payment_status)
        elif payment_status in ('canceled', 'expired', 'failed', 'timeout'):
            deposit.status = 'timeout'
            db.session.add(deposit)
            db.session.commit()
        elif payment_status == 'pending':
            deposit.status = 'pending'
            db.session.add(deposit)
            db.session.commit()
    except Exception:
        current_app.logger.exception('Deposit webhook processing failed for payment_id=%s', payment_id)
        return jsonify({'error': 'Webhook processing failed.'}), 500

    return 'ok', 200


@nowpayments_bp.route('/success')
def success():
    return render_template('deposit/success.html')


@deposit_bp.route('/')
@login_required
def index():
    """Deposit dashboard."""
    HistoryService.archive_due_items(user_id=current_user.id)
    page = request.args.get('page', 1, type=int)
    deposits = DepositService.get_user_deposits(current_user.id, page=page, per_page=20)

    return render_template(
        'deposit/index.html',
        deposits=deposits,
    )


@deposit_bp.route('/create', methods=['POST'])
@login_required
def create():
    """Legacy deposit creation is disabled. Use NowPayments deposit flow."""
    flash('Legacy deposit creation is disabled. Please use the new deposit form.', 'warning')
    return redirect(url_for('deposit.index'))


@deposit_bp.route('/<int:deposit_id>')
@login_required
def view(deposit_id):
    """Status page for a specific NowPayments deposit."""
    deposit = DepositService.get_deposit_by_id(deposit_id)

    if not deposit or deposit.user_id != current_user.id:
        flash('Deposit not found', 'error')
        return redirect(url_for('deposit.index'))

    coin_type = deposit.coin_type or 'USDT'
    expected_amount = deposit.expected_amount if deposit.expected_amount is not None else deposit.usdt_amount
    requested_amount = deposit.amount if deposit.amount is not None else deposit.usdt_amount

    now = utc_now()
    seconds_left = 0
    if deposit.expires_at:
        seconds_left = max(0, int((deposit.expires_at - now).total_seconds()))

    return render_template(
        'deposit/payment.html',
        deposit=deposit,
        coin_type=coin_type,
        expected_amount_display=_format_usdt(expected_amount),
        amount_display=_format_usdt(requested_amount),
        payment_reference=deposit.payment_id,
        seconds_left=seconds_left,
    )


@deposit_bp.route('/<int:deposit_id>/status')
@login_required
def status(deposit_id):
    """Small polling endpoint for payment page status updates."""
    deposit = DepositService.get_deposit_by_id(deposit_id)

    if not deposit or deposit.user_id != current_user.id:
        return jsonify({'error': 'Deposit not found'}), 404

    now = utc_now()
    seconds_left = 0
    if deposit.expires_at:
        seconds_left = max(0, int((deposit.expires_at - now).total_seconds()))

    return jsonify({
        'id': deposit.id,
        'status': deposit.status,
        'payment_id': deposit.payment_id,
        'seconds_left': seconds_left,
    })
