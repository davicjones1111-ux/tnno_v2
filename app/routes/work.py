"""
Work Routes
Work requests and service orders
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from app.extensions import db
from app.models import WorkRequest, ServiceOrder, WithdrawRequest
from app.services.history_service import HistoryService
from app.services import DepositService
from app.services.wallet_service import WalletService
from app.utils import save_uploaded_file_any
from app.validators import ValidationError

work_bp = Blueprint('work', __name__)

# ==================== Service Catalog Rules ====================

SERVICE_CATALOG = {
    'TikTok': ['Followers', 'Likes', 'Comments', 'Shares'],
    'YouTube': ['Subscribers', 'Likes', 'Comments', 'Shares'],
    'Telegram': ['Group Followers', 'Channel Followers'],
    'Facebook': ['Followers', 'Likes', 'Comments', 'Shares'],
    'Twitter': ['Followers', 'Likes', 'Comments', 'Shares'],
    'Instagram': ['Followers', 'Likes', 'Comments', 'Shares'],
}

PREMIUM_SERVICES = {'Followers', 'Subscribers', 'Group Followers', 'Channel Followers'}
PREMIUM_PRICE_TNNO = 200
ENGAGEMENT_PRICE_TNNO = 100
MIN_ORDER_QTY = 10
MAX_ORDER_QTY = 100000
WORK_UPLOAD_EXTENSIONS = {
    'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf', 'txt', 'csv',
    'doc', 'docx', 'zip', 'rar'
}


def get_service_price(service_name):
    """Get TNNO unit price for a valid service name."""
    if service_name in PREMIUM_SERVICES:
        return PREMIUM_PRICE_TNNO
    return ENGAGEMENT_PRICE_TNNO


def build_order_context(form_data=None):
    """Shared template context for service catalog/order pages."""
    return {
        'service_catalog': SERVICE_CATALOG,
        'premium_services': list(PREMIUM_SERVICES),
        'premium_price_tnno': PREMIUM_PRICE_TNNO,
        'engagement_price_tnno': ENGAGEMENT_PRICE_TNNO,
        'min_order_qty': MIN_ORDER_QTY,
        'max_order_qty': MAX_ORDER_QTY,
        'usdt_to_points': current_app.config.get('USDT_TO_POINTS', 4000),
        'form_data': form_data or {},
    }


def get_work_request_fee():
    """Return the TNNO fee for a work request."""
    return int(current_app.config.get('WORK_REQUEST_FEE_TNNO', 10000))


@work_bp.route('/')

@login_required
def index():
    """Work requests home"""
    return render_template('work/index.html', request_fee=get_work_request_fee())


# ==================== Work Requests ====================

@work_bp.route('/requests')
@login_required
def requests():
    """My work requests"""
    # Check if user is logged in
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    HistoryService.archive_due_items(user_id=current_user.id)
    status = request.args.get('status')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    query = WorkRequest.query.filter_by(user_id=current_user.id).filter(WorkRequest.is_archived.is_(False))
    if status:
        query = query.filter_by(status=status)
    
    work_requests = query.order_by(WorkRequest.created_at.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('work/requests.html', work_requests=work_requests)


@work_bp.route('/requests/create', methods=['GET', 'POST'])

@login_required
def create_request():
    """Create work request"""
    # Check if user is logged in
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        file = request.files.get('file')

        if not message:
            flash('Message is required', 'error')
            return redirect(url_for('work.create_request'))

        request_fee = get_work_request_fee()
        if current_user.coins < request_fee:
            flash(
                f'Insufficient TNNO. Need {request_fee:,} to submit a work request.',
                'error'
            )
            return redirect(url_for('work.create_request'))
        
        # Handle file upload
        file_path = None
        if file and file.filename:
            try:
                file_path = save_uploaded_file_any(file, 'work', WORK_UPLOAD_EXTENSIONS)
            except ValueError as exc:
                flash(str(exc), 'error')
                return redirect(url_for('work.create_request'))
            if not file_path:
                flash('Please upload a supported file type', 'error')
                return redirect(url_for('work.create_request'))
        
        # Create work request
        work_req = WorkRequest(
            user_id=current_user.id,
            message=message,
            file_path=file_path,
            status='pending'
        )
        db.session.add(work_req)
        db.session.commit()
        
        flash('Work request submitted!', 'success')
        return redirect(url_for('work.requests'))

    return render_template('work/create_request.html', request_fee=get_work_request_fee())


@work_bp.route('/requests/<int:request_id>')

@login_required
def view_request(request_id):
    """View work request details"""
    # Check if user is logged in
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    work_req = WorkRequest.query.get_or_404(request_id)
    
    # Check ownership
    if work_req.user_id != current_user.id and not current_user.is_admin():
        flash('Access denied', 'error')
        return redirect(url_for('work.requests'))
    
    return render_template(
        'work/view_request.html',
        work_request=work_req,
        request_fee=get_work_request_fee()
    )


# ==================== Service Orders ====================

@work_bp.route('/orders')

@login_required
def orders():
    """My service orders"""
    # Check if user is logged in
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    HistoryService.archive_due_items(user_id=current_user.id)
    status = request.args.get('status')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    query = ServiceOrder.query.filter_by(user_id=current_user.id).filter(ServiceOrder.is_archived.is_(False))
    if status:
        query = query.filter_by(status=status)
    
    orders = query.order_by(ServiceOrder.created_at.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('work/orders.html', orders=orders)


@work_bp.route('/orders/create', methods=['GET', 'POST'])

@login_required
def create_order():
    """Create service order"""
    # Check if user is logged in
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    if request.method == 'POST':
        category = request.form.get('category', '').strip()
        service = request.form.get('service', '').strip()
        link = request.form.get('link', '').strip()
        quantity = request.form.get('quantity', MIN_ORDER_QTY, type=int)

        form_data = {
            'category': category,
            'service': service,
            'link': link,
            'quantity': quantity,
        }
        
        if not category or not service:
            flash('Category and service are required', 'error')
            return render_template('work/create_order.html', **build_order_context(form_data))

        valid_services = SERVICE_CATALOG.get(category)
        if not valid_services:
            flash('Invalid platform selected', 'error')
            return render_template('work/create_order.html', **build_order_context(form_data))

        if service not in valid_services:
            flash('Invalid service type for selected platform', 'error')
            return render_template('work/create_order.html', **build_order_context(form_data))

        if quantity < MIN_ORDER_QTY or quantity > MAX_ORDER_QTY:
            flash(f'Quantity must be between {MIN_ORDER_QTY:,} and {MAX_ORDER_QTY:,}', 'error')
            return render_template('work/create_order.html', **build_order_context(form_data))

        unit_price = get_service_price(service)
        charge = quantity * unit_price
        
        if charge <= 0:
            flash('Invalid charge amount', 'error')
            return render_template('work/create_order.html', **build_order_context(form_data))
        
        try:
            WalletService.debit_user(
                user_id=current_user.id,
                amount=charge,
                transaction_type='service_order_charge',
                details=f'{category}:{service}',
            )
            order = ServiceOrder(
                user_id=current_user.id,
                category=category,
                service=service,
                link=link if link else None,
                quantity=quantity,
                charge=charge,
                status='pending'
            )
            db.session.add(order)
            db.session.flush()
            WalletService.record_transaction(
                user_id=current_user.id,
                amount=0,
                transaction_type='service_order_created',
                status='pending',
                reference_type='service_order',
                reference_id=order.id,
                details=f'{category}:{service}',
            )
            db.session.commit()
        except ValidationError:
            db.session.rollback()
            flash(
                f'Insufficient TNNO. Need {charge:,}, you have {current_user.coins:,}.',
                'error'
            )
            return render_template('work/create_order.html', **build_order_context(form_data))
        
        flash(f'Service order created! Charged {charge:,} TNNO.', 'success')
        return redirect(url_for('work.orders'))
    
    return render_template(
        'work/create_order.html',
        **build_order_context({'quantity': MIN_ORDER_QTY})
    )


@work_bp.route('/orders/<int:order_id>')

@login_required
def view_order(order_id):
    """View service order details"""
    # Check if user is logged in
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    order = ServiceOrder.query.get_or_404(order_id)
    
    # Check ownership
    if order.user_id != current_user.id and not current_user.is_admin():
        flash('Access denied', 'error')
        return redirect(url_for('work.orders'))
    
    return render_template('work/view_order.html', order=order)


# ==================== Withdraw Requests ====================

@work_bp.route('/withdraw')
@login_required
def withdraw():
    """My withdrawal requests"""
    # Check if user is logged in
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    from app.models import WithdrawRequest
    HistoryService.archive_due_items(user_id=current_user.id)
    
    status = request.args.get('status')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    query = WithdrawRequest.query.filter_by(user_id=current_user.id).filter(WithdrawRequest.is_archived.is_(False))
    if status:
        query = query.filter_by(status=status)
    
    withdraws = query.order_by(WithdrawRequest.created_at.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('work/withdraw.html', withdraws=withdraws)


@work_bp.route('/withdraw/create', methods=['GET', 'POST'])

@login_required
def create_withdraw():
    """Create withdrawal request"""
    # Check if user is logged in
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    from app.models import WithdrawRequest
    
    if request.method == 'POST':
        amount = request.form.get('amount', 0, type=int)
        wallet = (
            request.form.get('wallet', '').strip()
            or request.form.get('wallet_address', '').strip()
            or request.form.get('destination', '').strip()
        )
        name = request.form.get('name', '').strip()
        network = request.form.get('network', '').strip()
        
        try:
            WalletService.create_withdrawal(
                user_id=current_user.id,
                amount=amount,
                wallet=wallet,
                name=name,
                network=network or None,
            )
        except ValidationError as exc:
            flash(str(exc), 'error')
            return redirect(url_for('work.create_withdraw'))
        
        flash('Withdrawal request submitted!', 'success')
        return redirect(url_for('work.withdraw'))
    
    return render_template('work/create_withdraw.html')


@work_bp.route('/finance')
@login_required
def finance():
    """Combined deposit and withdraw page"""
    HistoryService.archive_due_items(user_id=current_user.id)

    # Get recent deposits with pagination
    deposits = DepositService.get_user_deposits(current_user.id, page=1, per_page=10)

    # Get recent withdrawals
    withdrawals_list = WithdrawRequest.query.filter_by(user_id=current_user.id).filter(WithdrawRequest.is_archived.is_(False)).order_by(WithdrawRequest.created_at.desc()).limit(10).all()

    return render_template(
        'work/finance.html',
        deposits=deposits,
        withdrawals=withdrawals_list,
        request_fee=get_work_request_fee()
    )
