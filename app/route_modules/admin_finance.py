"""
Admin finance and operations routes.
"""
from __future__ import annotations

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.extensions import db
from app.models import ServiceOrder, User, WithdrawRequest, WorkRequest
from app.services.history_service import HistoryService
from app.services.wallet_service import WalletService
from app.validators import ValidationError


def register_admin_finance_routes(admin_bp, admin_required):
    @admin_bp.route('/withdrawals')
    @login_required
    def withdrawals():
        """Manage withdrawal requests."""
        if not admin_required():
            flash('Access denied', 'error')
            return redirect(url_for('missions.index'))

        status = request.args.get('status', 'pending')
        if status != 'pending':
            status = 'pending'
        page = request.args.get('page', 1, type=int)
        per_page = 20
        query = WithdrawRequest.query.filter(WithdrawRequest.is_archived.is_(False))
        if status:
            query = query.filter_by(status=status)
        withdraws = query.order_by(WithdrawRequest.created_at.desc())\
            .paginate(page=page, per_page=per_page, error_out=False)
        return render_template('admin/withdrawals.html', withdraws=withdraws)

    @admin_bp.route('/withdrawals/<int:withdraw_id>/approve', methods=['POST'])
    @login_required
    def approve_withdrawal(withdraw_id):
        """Approve withdrawal request."""
        if not admin_required():
            flash('Access denied', 'error')
            return redirect(url_for('missions.index'))
        withdraw = WithdrawRequest.query.get_or_404(withdraw_id)
        withdraw.status = 'approved'
        HistoryService.mark_archived_if_terminal(withdraw, 'withdrawals')
        db.session.commit()
        flash('Withdrawal approved!', 'success')
        return redirect(url_for('admin.withdrawals'))

    @admin_bp.route('/withdrawals/<int:withdraw_id>/reject', methods=['POST'])
    @login_required
    def reject_withdrawal(withdraw_id):
        """Reject withdrawal request."""
        if not admin_required():
            flash('Access denied', 'error')
            return redirect(url_for('missions.index'))
        withdraw = WithdrawRequest.query.get_or_404(withdraw_id)
        withdraw.status = 'rejected'
        HistoryService.mark_archived_if_terminal(withdraw, 'withdrawals')
        WalletService.credit_user(
            user_id=withdraw.user_id,
            amount=withdraw.amount,
            transaction_type='withdraw_rejected_refund',
            reference_type='withdraw_request',
            reference_id=withdraw.id,
            details='admin_rejected',
        )
        db.session.commit()
        flash('Withdrawal rejected and refunded', 'success')
        return redirect(url_for('admin.withdrawals'))

    @admin_bp.route('/work-requests')
    @login_required
    def work_requests():
        """Manage work requests."""
        if not admin_required():
            flash('Access denied', 'error')
            return redirect(url_for('missions.index'))
        status = request.args.get('status', 'pending')
        if status != 'pending':
            status = 'pending'
        page = request.args.get('page', 1, type=int)
        per_page = 20
        query = WorkRequest.query.filter(WorkRequest.is_archived.is_(False))
        if status:
            query = query.filter_by(status=status)
        work_reqs = query.order_by(WorkRequest.created_at.desc())\
            .paginate(page=page, per_page=per_page, error_out=False)
        return render_template('admin/work_requests.html', work_requests=work_reqs)

    @admin_bp.route('/work-requests/<int:request_id>/accept', methods=['POST'])
    @login_required
    def accept_work_request(request_id):
        """Accept work request and charge TNNO fee."""
        if not admin_required():
            flash('Access denied', 'error')
            return redirect(url_for('missions.index'))

        work_req = WorkRequest.query.get_or_404(request_id)
        if work_req.status != 'pending':
            flash('Only pending work requests can be accepted', 'error')
            return redirect(url_for('admin.work_requests'))

        request_fee = int(current_app.config.get('WORK_REQUEST_FEE_TNNO', 10000))
        try:
            WalletService.debit_user(
                user_id=work_req.user_id,
                amount=request_fee,
                transaction_type='work_request_accept_charge',
                reference_type='work_request',
                reference_id=work_req.id,
            )
        except ValidationError:
            db.session.rollback()
            flash('User does not have enough TNNO to accept this request', 'error')
            return redirect(url_for('admin.work_requests'))

        work_req.status = 'accepted'
        HistoryService.mark_archived_if_terminal(work_req, 'work_requests')
        db.session.commit()
        flash('Work request accepted and fee charged', 'success')
        return redirect(url_for('admin.work_requests'))

    @admin_bp.route('/work-requests/<int:request_id>/reject', methods=['POST'])
    @login_required
    def reject_work_request(request_id):
        """Reject work request."""
        if not admin_required():
            flash('Access denied', 'error')
            return redirect(url_for('missions.index'))
        work_req = WorkRequest.query.get_or_404(request_id)
        if work_req.status != 'pending':
            flash('Only pending work requests can be rejected', 'error')
            return redirect(url_for('admin.work_requests'))
        work_req.status = 'rejected'
        HistoryService.mark_archived_if_terminal(work_req, 'work_requests')
        db.session.commit()
        flash('Work request rejected', 'success')
        return redirect(url_for('admin.work_requests'))

    @admin_bp.route('/service-orders')
    @login_required
    def service_orders():
        """Manage service orders."""
        if not admin_required():
            flash('Access denied', 'error')
            return redirect(url_for('missions.index'))
        status = request.args.get('status', 'pending')
        if status != 'pending':
            status = 'pending'
        page = request.args.get('page', 1, type=int)
        per_page = 20
        query = ServiceOrder.query.filter(ServiceOrder.is_archived.is_(False))
        if status:
            query = query.filter_by(status=status)
        orders = query.order_by(ServiceOrder.created_at.desc())\
            .paginate(page=page, per_page=per_page, error_out=False)
        return render_template('admin/service_orders.html', orders=orders)

    @admin_bp.route('/service-orders/<int:order_id>/accept', methods=['POST'])
    @login_required
    def accept_service_order(order_id):
        """Accept service order."""
        if not admin_required():
            flash('Access denied', 'error')
            return redirect(url_for('missions.index'))
        order = ServiceOrder.query.get_or_404(order_id)
        if order.status != 'pending':
            flash('Only pending service orders can be accepted', 'error')
            return redirect(url_for('admin.service_orders'))
        order.status = 'completed'
        HistoryService.mark_archived_if_terminal(order, 'service_orders')
        db.session.commit()
        flash('Service order accepted', 'success')
        return redirect(url_for('admin.service_orders'))

    @admin_bp.route('/service-orders/<int:order_id>/reject', methods=['POST'])
    @login_required
    def reject_service_order(order_id):
        """Reject service order and refund user TNNO."""
        if not admin_required():
            flash('Access denied', 'error')
            return redirect(url_for('missions.index'))
        order = ServiceOrder.query.get_or_404(order_id)
        if order.status != 'pending':
            flash('Only pending service orders can be rejected', 'error')
            return redirect(url_for('admin.service_orders'))
        order.status = 'rejected'
        HistoryService.mark_archived_if_terminal(order, 'service_orders')
        WalletService.credit_user(
            user_id=order.user_id,
            amount=order.charge,
            transaction_type='service_order_rejected_refund',
            reference_type='service_order',
            reference_id=order.id,
            details='admin_rejected',
        )
        db.session.commit()
        flash('Service order rejected and refunded', 'success')
        return redirect(url_for('admin.service_orders'))
