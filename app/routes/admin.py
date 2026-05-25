"""
Admin Routes
Admin panel for system management
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, jsonify
from flask_login import login_required, current_user
from app.extensions import db
from app.models import User, Mission, UserMission, Deposit, WithdrawRequest, WorkRequest, ServiceOrder, Product, MerchOrder, SellerRequest, SellerReport, UserNotification, AuthEvent, AdminAuditLog
from app.services.seller_service import SellerService
from app.services import MissionService, DepositService
from app.datetime_utils import utc_now
from app.services.history_service import HistoryService
from app.services.session_service import SessionService
from app.services.wallet_service import WalletService
from app.route_modules.admin_finance import register_admin_finance_routes
from app.validators import ValidationError
from sqlalchemy import func
from datetime import datetime, timedelta

admin_bp = Blueprint('admin', __name__)


def admin_required():
    """Check if current user is admin"""
    if not current_user.is_authenticated:
        return False
    return current_user.is_admin()


def _log_admin_action(action, *, target_type=None, target_id=None, details=None):
    try:
        SessionService.log_admin_action(
            admin_user=current_user,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
        )
    except Exception as exc:
        db.session.rollback()
        current_app.logger.warning('Admin audit log failed for action=%s: %s', action, exc)


@admin_bp.route('/')
@login_required
def index():
    """Admin dashboard"""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))
    
    # Get statistics
    total_users = User.query.count()
    total_missions = Mission.query.count()
    active_missions = Mission.query.filter_by(status='active').count()
    pending_deposits = Deposit.query.filter_by(status='pending').count()
    pending_withdraws = WithdrawRequest.query.filter_by(status='pending').count()
    pending_submissions = UserMission.query.filter_by(status='pending').count()
    pending_work_requests = WorkRequest.query.filter_by(status='pending').count()
    pending_service_orders = ServiceOrder.query.filter_by(status='pending').count()
    total_merch_products = Product.query.count()
    pending_seller_requests = SellerRequest.query.filter_by(status='pending').count()
    pending_seller_reports = SellerReport.query.filter_by(status='pending').count()
    pending_notifications = UserNotification.query.filter(UserNotification.read_at.is_(None)).count()
    total_site_coins = db.session.query(func.coalesce(func.sum(User.coins), 0)).scalar() or 0
    last_24h = utc_now() - timedelta(hours=24)
    suspicious_events_count = AuthEvent.query.filter(
        AuthEvent.created_at >= last_24h,
        AuthEvent.status.in_(('warning', 'error'))
    ).count()
    audit_actions_count = AdminAuditLog.query.filter(AdminAuditLog.created_at >= last_24h).count()

    # Recent activity
    recent_deposits = Deposit.query.order_by(Deposit.created_at.desc()).limit(10).all()
    recent_withdraws = WithdrawRequest.query.order_by(WithdrawRequest.created_at.desc()).limit(10).all()

    return render_template('admin/index.html',
                         total_users=total_users,
                         total_missions=total_missions,
                         active_missions=active_missions,
                         pending_deposits=pending_deposits,
                         pending_withdraws=pending_withdraws,
                         pending_submissions=pending_submissions,
                         pending_work_requests=pending_work_requests,
                         pending_service_orders=pending_service_orders,
                         total_merch_products=total_merch_products,
                         pending_seller_requests=pending_seller_requests,
                         pending_seller_reports=pending_seller_reports,
                         pending_notifications=pending_notifications,
                         total_site_coins=total_site_coins,
                         suspicious_events_count=suspicious_events_count,
                         audit_actions_count=audit_actions_count,
                         recent_deposits=recent_deposits,
                         recent_withdraws=recent_withdraws)


@admin_bp.route('/security')
@login_required
def security():
    """Review suspicious authentication activity and admin audit history."""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))

    status = (request.args.get('status') or 'all').strip().lower()
    hours = request.args.get('hours', 24, type=int)
    hours = min(max(hours, 1), 168)
    since = utc_now() - timedelta(hours=hours)

    auth_query = AuthEvent.query.filter(AuthEvent.created_at >= since)
    if status in {'warning', 'error', 'success', 'info'}:
        auth_query = auth_query.filter(AuthEvent.status == status)

    auth_events = auth_query.order_by(AuthEvent.created_at.desc()).limit(100).all()
    audit_logs = AdminAuditLog.query.filter(AdminAuditLog.created_at >= since)\
        .order_by(AdminAuditLog.created_at.desc())\
        .limit(100)\
        .all()

    summary = {
        'failed_logins': AuthEvent.query.filter(
            AuthEvent.created_at >= since,
            AuthEvent.event_type.in_(('login_failure', 'login_throttled', 'login_rate_limited'))
        ).count(),
        'otp_failures': AuthEvent.query.filter(
            AuthEvent.created_at >= since,
            AuthEvent.event_type == 'otp_failure'
        ).count(),
        'new_device_logins': AuthEvent.query.filter(
            AuthEvent.created_at >= since,
            AuthEvent.event_type == 'login_success',
            AuthEvent.details == 'new_device'
        ).count(),
        'warning_events': AuthEvent.query.filter(
            AuthEvent.created_at >= since,
            AuthEvent.status.in_(('warning', 'error'))
        ).count(),
        'admin_actions': AdminAuditLog.query.filter(AdminAuditLog.created_at >= since).count(),
    }

    return render_template(
        'admin/security.html',
        auth_events=auth_events,
        audit_logs=audit_logs,
        summary=summary,
        selected_status=status,
        selected_hours=hours,
    )


@admin_bp.route('/seller-reports')
@login_required
def seller_reports():
    """View seller reports."""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))

    status = request.args.get('status', 'pending')
    if status != 'pending':
        status = 'pending'
    query = SellerReport.query
    if status:
        query = query.filter_by(status=status)
    reports = query.order_by(SellerReport.created_at.desc()).all()
    return render_template('admin/seller_reports.html', reports=reports, status=status)


@admin_bp.route('/seller-reports/<int:report_id>/review', methods=['POST'])
@login_required
def review_seller_report(report_id):
    """Mark report as reviewed."""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))

    report = SellerReport.query.get_or_404(report_id)
    if report.status != 'reviewed':
        report.status = 'reviewed'
        report.reviewed_at = utc_now()
        report.reviewed_by = current_user.id
        db.session.commit()
        _log_admin_action('seller_report_reviewed', target_type='seller_report', target_id=report.id, details=f'seller_id={report.seller_id}')

    flash('Report marked as reviewed.', 'success')
    return redirect(url_for('admin.seller_reports'))


@admin_bp.route('/notifications', methods=['GET', 'POST'])
@login_required
def notifications():
    """Send notifications to users."""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))

    if request.method == 'POST':
        user_id = request.form.get('user_id', type=int)
        user_query = (request.form.get('user_query') or '').strip()
        message = (request.form.get('message') or '').strip()
        attachment = request.files.get('attachment')

        if not user_id and not user_query:
            flash('Select a user and enter a message.', 'error')
            return redirect(url_for('admin.notifications'))

        user = None
        if user_id:
            user = User.query.get(user_id)
        if not user and user_query:
            if user_query.isdigit():
                user = User.query.get(int(user_query))
                if not user:
                    user = User.query.filter_by(user_6digit=user_query).first()
            else:
                user = User.query.filter_by(username=user_query).first()

        if not user:
            flash('User not found.', 'error')
            return redirect(url_for('admin.notifications'))

        attachment_path = None
        if attachment and attachment.filename:
            from app.utils import save_uploaded_file_any
            allowed = current_app.config.get('NOTIFICATION_ALLOWED_EXTENSIONS', set())
            try:
                attachment_path = save_uploaded_file_any(attachment, 'notifications', allowed)
            except ValueError as exc:
                flash(str(exc), 'error')
                return redirect(url_for('admin.notifications'))
            if not attachment_path:
                flash('Attachment type not allowed.', 'error')
                return redirect(url_for('admin.notifications'))

        notif = UserNotification(
            user_id=user.id,
            message=message,
            attachment_path=attachment_path,
            sent_by=current_user.id
        )
        db.session.add(notif)
        db.session.commit()
        _log_admin_action('user_notification_sent', target_type='user', target_id=user.id, details=f'notification_id={notif.id}')
        flash('Notification sent.', 'success')
        return redirect(url_for('admin.notifications'))

    recent_notifications = UserNotification.query.order_by(UserNotification.created_at.desc()).limit(50).all()
    return render_template('admin/notifications.html', notifications=recent_notifications)


@admin_bp.route('/notifications/search')
@login_required
def notifications_search():
    """Search users for notifications."""
    if not admin_required():
        return jsonify({'results': []}), 403

    q = (request.args.get('q') or '').strip()
    if not q:
        return jsonify({'results': []})

    query = User.query
    if q.isdigit():
        query = query.filter((User.id == int(q)) | (User.user_6digit == q))
    else:
        query = query.filter(User.username.ilike(f'%{q}%'))

    users = query.order_by(User.username.asc()).limit(10).all()
    results = [{
        'id': u.id,
        'username': u.username,
        'user_6digit': u.user_6digit or ''
    } for u in users]
    return jsonify({'results': results})


@admin_bp.route('/users')
@login_required
def users():
    """Manage users"""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))
    
    page = request.args.get('page', 1, type=int)
    per_page = 50
    query = (request.args.get('q') or '').strip()

    # Seller sales stats (total + current month)
    start_of_month = utc_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    sales_rows = db.session.query(
        Product.seller_id.label('seller_id'),
        func.coalesce(func.sum(MerchOrder.total_price), 0).label('total_sales'),
        func.count(MerchOrder.id).label('total_orders')
    ).join(Product, Product.id == MerchOrder.product_id).filter(
        Product.seller_id.isnot(None),
        MerchOrder.status == 'completed'
    ).group_by(Product.seller_id).all()

    monthly_rows = db.session.query(
        Product.seller_id.label('seller_id'),
        func.coalesce(func.sum(MerchOrder.total_price), 0).label('monthly_sales'),
        func.count(MerchOrder.id).label('monthly_orders')
    ).join(Product, Product.id == MerchOrder.product_id).filter(
        Product.seller_id.isnot(None),
        MerchOrder.status == 'completed',
        MerchOrder.purchased_at >= start_of_month
    ).group_by(Product.seller_id).all()

    seller_stats = {}
    for row in sales_rows:
        seller_stats[row.seller_id] = {
            'total_sales': int(row.total_sales or 0),
            'total_orders': int(row.total_orders or 0),
            'monthly_sales': 0,
            'monthly_orders': 0
        }
    for row in monthly_rows:
        entry = seller_stats.setdefault(row.seller_id, {
            'total_sales': 0,
            'total_orders': 0,
            'monthly_sales': 0,
            'monthly_orders': 0
        })
        entry['monthly_sales'] = int(row.monthly_sales or 0)
        entry['monthly_orders'] = int(row.monthly_orders or 0)
    
    users_query = User.query
    if query:
        if query.isdigit():
            users_query = users_query.filter(
                (User.id == int(query)) | (User.user_6digit == query)
            )
        else:
            users_query = users_query.filter(User.username.ilike(f'%{query}%'))

    users = users_query.order_by(User.created_at.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template(
        'admin/users.html',
        users=users,
        seller_stats=seller_stats,
        sales_month_label=start_of_month.strftime('%B %Y'),
        q=query
    )


@admin_bp.route('/seller-requests')
@login_required
def seller_requests():
    """Review seller access requests."""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))

    status = request.args.get('status', 'pending')
    query = SellerRequest.query
    if status:
        query = query.filter_by(status=status)

    requests_list = query.order_by(SellerRequest.created_at.desc()).all()
    return render_template('admin/seller_requests.html', requests=requests_list, status=status)


@admin_bp.route('/seller-requests/<int:req_id>')
@login_required
def seller_request_detail(req_id):
    """View seller request details."""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))

    req = SellerRequest.query.get_or_404(req_id)
    return render_template('admin/seller_request_detail.html', req=req)


@admin_bp.route('/seller-requests/<int:req_id>/approve', methods=['POST'])
@login_required
def approve_seller_request(req_id):
    """Approve seller request."""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))

    req = SellerRequest.query.get_or_404(req_id)
    if req.status != 'approved':
        req.status = 'approved'
        req.reviewed_at = utc_now()
        req.reviewed_by = current_user.id
        user = User.query.get(req.user_id)
        if user:
            user.is_seller = True
            if req.plan_months and req.plan_months > 0:
                user.seller_expires_at = SellerService.compute_new_expiry(
                    user.seller_expires_at,
                    int(req.plan_months)
                )
                user.seller_reminder_sent_at = None
        db.session.commit()
        _log_admin_action('seller_request_approved', target_type='seller_request', target_id=req.id, details=f'user_id={req.user_id}')

    flash('Seller request approved.', 'success')
    return redirect(url_for('admin.seller_requests'))


@admin_bp.route('/seller-requests/<int:req_id>/reject', methods=['POST'])
@login_required
def reject_seller_request(req_id):
    """Reject seller request."""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))

    req = SellerRequest.query.get_or_404(req_id)
    if req.status != 'rejected':
        was_pending = req.status == 'pending'
        req.status = 'rejected'
        req.reviewed_at = utc_now()
        req.reviewed_by = current_user.id
        if was_pending:
            user = User.query.get(req.user_id)
            if user and req.plan_cost:
                user.coins += int(req.plan_cost)
        db.session.commit()
        _log_admin_action('seller_request_rejected', target_type='seller_request', target_id=req.id, details=f'user_id={req.user_id}')

    flash('Seller request rejected.', 'success')
    return redirect(url_for('admin.seller_requests'))


@admin_bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_user(user_id):
    """Edit user"""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))
    
    user = User.query.get_or_404(user_id)
    start_of_month = utc_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    if request.method == 'POST':
        coins = request.form.get('coins', 0, type=int)
        seller_flag = request.form.get('seller') == 'on'
        commission_rate = request.form.get('commission_rate', 3.0, type=float) if seller_flag else 0.0
        commission_rate = max(0.0, min(commission_rate, 100.0))
        current_balance = int(user.coins or 0)
        target_balance = max(coins or 0, 0)

        if target_balance > current_balance:
            WalletService.credit_user(
                user_id=user.id,
                amount=target_balance - current_balance,
                transaction_type='admin_balance_set_increase',
                reference_type='admin_user',
                reference_id=current_user.id,
                details=f'Balance set by admin {current_user.username} to {target_balance} TNNO',
            )
        elif target_balance < current_balance:
            WalletService.debit_user(
                user_id=user.id,
                amount=current_balance - target_balance,
                transaction_type='admin_balance_set_decrease',
                reference_type='admin_user',
                reference_id=current_user.id,
                details=f'Balance set by admin {current_user.username} to {target_balance} TNNO',
            )

        user.is_seller = seller_flag
        if seller_flag:
            user.seller_commission_rate = commission_rate / 100  # Convert percentage to decimal
        else:
            user.seller_commission_rate = 0.0
        db.session.commit()
        _log_admin_action(
            'user_updated',
            target_type='user',
            target_id=user.id,
            details=f'seller={user.is_seller},coins={int(user.coins or 0)},commission_rate={user.seller_commission_rate}',
        )
        
        flash('User updated successfully!', 'success')
        return redirect(url_for('admin.users'))

    # Seller sales stats for this user
    total_sales, total_orders = db.session.query(
        func.coalesce(func.sum(MerchOrder.total_price), 0),
        func.count(MerchOrder.id)
    ).join(Product, Product.id == MerchOrder.product_id).filter(
        Product.seller_id == user.id,
        MerchOrder.status == 'completed'
    ).first()

    monthly_sales, monthly_orders = db.session.query(
        func.coalesce(func.sum(MerchOrder.total_price), 0),
        func.count(MerchOrder.id)
    ).join(Product, Product.id == MerchOrder.product_id).filter(
        Product.seller_id == user.id,
        MerchOrder.status == 'completed',
        MerchOrder.purchased_at >= start_of_month
    ).first()
    
    return render_template(
        'admin/edit_user.html',
        user=user,
        seller_sales_total=int(total_sales or 0),
        seller_orders_total=int(total_orders or 0),
        seller_sales_month=int(monthly_sales or 0),
        seller_orders_month=int(monthly_orders or 0),
        sales_month_label=start_of_month.strftime('%B %Y')
    )


@admin_bp.route('/users/<int:user_id>/grant-coins', methods=['POST'])
@login_required
def grant_user_coins(user_id):
    """Grant TNNO to a user without overwriting the existing balance."""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))

    user = User.query.get_or_404(user_id)
    amount = request.form.get('grant_amount', type=int)
    reason = (request.form.get('grant_reason') or '').strip()

    try:
        WalletService.credit_user(
            user_id=user.id,
            amount=amount,
            transaction_type='admin_coin_grant',
            reference_type='admin_user',
            reference_id=current_user.id,
            details=f'Granted by admin {current_user.username}' + (f': {reason}' if reason else ''),
            commit=True,
        )
    except ValidationError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('admin.edit_user', user_id=user.id) + '#wallet-grant')

    _log_admin_action(
        'user_coin_grant',
        target_type='user',
        target_id=user.id,
        details=f'amount={amount},reason={reason or ""}',
    )
    flash(f'Added {amount} TNNO to {user.username}. New balance: {int(user.coins or 0)} TNNO.', 'success')
    return redirect(url_for('admin.edit_user', user_id=user.id) + '#wallet-grant')


@admin_bp.route('/users/<int:user_id>/seller', methods=['POST'])
@login_required
def toggle_user_seller(user_id):
    """Quick-toggle seller access for a user."""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))

    user = User.query.get_or_404(user_id)
    make_seller = request.form.get('make_seller') == '1'
    commission_rate = request.form.get('commission_rate', 3.0, type=float)
    commission_rate = max(0.0, min(commission_rate, 100.0))

    user.is_seller = make_seller
    user.seller_commission_rate = (commission_rate / 100.0) if make_seller else 0.0
    if make_seller and user.seller_expires_at is None:
        user.seller_reminder_sent_at = None
    db.session.commit()
    _log_admin_action(
        'seller_toggle',
        target_type='user',
        target_id=user.id,
        details=f'make_seller={make_seller},commission_rate={commission_rate}',
    )

    flash(
        f'{user.username} is now {"a seller" if make_seller else "a regular user"}.',
        'success'
    )
    return redirect(url_for('admin.users', q=request.args.get('q', '')))


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
def delete_user(user_id):
    """Delete user account (admin only)"""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))
    
    user = User.query.get_or_404(user_id)
    
    # Prevent self-deletion
    if user.id == current_user.id:
        flash('Cannot delete your own account', 'error')
        return redirect(url_for('admin.edit_user', user_id=user_id))
    
    username = user.username
    _log_admin_action('user_delete_requested', target_type='user', target_id=user.id, details=f'username={username}')
    db.session.delete(user)
    db.session.commit()

    flash(f'User "{username}" deleted successfully!', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/missions')
@login_required
def missions():
    """Manage missions"""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))
    
    missions = Mission.query.order_by(Mission.created_at.desc()).all()
    return render_template('admin/missions.html', missions=missions)


@admin_bp.route('/submissions')
@login_required
def submissions():
    """Manage mission submissions"""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))
    
    status = request.args.get('status', 'pending')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    query = UserMission.query.filter(UserMission.is_archived.is_(False))
    if status:
        query = query.filter_by(status=status)
    
    submissions = query.order_by(UserMission.submission_time.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('admin/submissions.html', submissions=submissions)


@admin_bp.route('/submissions/<int:submission_id>')
@login_required
def view_submission(submission_id):
    """View submission details"""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))
    
    submission = UserMission.query.get_or_404(submission_id)
    return render_template('admin/view_submission.html', submission=submission)


@admin_bp.route('/submissions/<int:submission_id>/approve', methods=['POST'])
@login_required
def approve_submission(submission_id):
    """Approve mission submission"""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))
    
    success, message = MissionService.approve_submission(submission_id, current_user.id)
    if success:
        _log_admin_action('mission_submission_approved', target_type='user_mission', target_id=submission_id)
    flash(message, 'success' if success else 'error')
    
    return redirect(url_for('admin.submissions'))


@admin_bp.route('/submissions/<int:submission_id>/reject', methods=['POST'])
@login_required
def reject_submission(submission_id):
    """Reject mission submission"""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))
    
    success, message = MissionService.reject_submission(submission_id)
    if success:
        _log_admin_action('mission_submission_rejected', target_type='user_mission', target_id=submission_id)
    flash(message, 'success' if success else 'error')
    
    return redirect(url_for('admin.submissions'))


@admin_bp.route('/deposits')
@login_required
def deposits():
    """Manage deposits"""
    if not admin_required():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))
    
    status = request.args.get('status', 'pending')
    if status != 'pending':
        status = 'pending'
    if status == 'completed':
        status = 'success'
    elif status == 'cancelled':
        status = 'expired'
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    query = Deposit.query.filter(Deposit.is_archived.is_(False))
    if status:
        query = query.filter_by(status=status)
    
    deposits = query.order_by(Deposit.created_at.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('admin/deposits.html', deposits=deposits)


register_admin_finance_routes(admin_bp, admin_required)
