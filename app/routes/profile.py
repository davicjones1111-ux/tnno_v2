"""
Profile Routes
User profile management
"""
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from sqlalchemy import or_, func
from app.extensions import db, cache
from app.models import User, SellerRequest, SellerRating, Product, MerchOrder, UserNotification, SellerNotification, SellerChatConversation, SellerChatMessage
from app.security import clear_auth_cookies, consume_action_quota, get_safe_redirect_target, rotate_session_identifier
from app.services import UserService
from app.services.otp_service import OTPService
from app.services.session_service import SessionService
from app.services.seller_service import SellerService, SELLER_PLANS
from app.services.pagination_service import PaginationService
from app.services.wallet_service import WalletService
from app.datetime_utils import utc_now
from app.utils import save_uploaded_image_optimized
from app.validators import ValidationError, validate_email, validate_password, validate_username

profile_bp = Blueprint('profile', __name__)


def _latest_seller_request_for_current_user():
    return SellerRequest.query.filter_by(user_id=current_user.id)\
        .order_by(SellerRequest.created_at.desc())\
        .first()


def _settings_context():
    tracked_session = SessionService.get_current_session()
    return {
        'active_sessions': SessionService.list_user_sessions(current_user, limit=20),
        'recent_auth_events': SessionService.list_recent_auth_events(current_user, limit=20),
        'current_session_id': tracked_session.id if tracked_session else None,
        'email_delivery_enabled': bool(current_app.config.get('RESEND_API_KEY')),
    }


def _render_settings(status_code: int = 200):
    return render_template('profile/settings.html', **_settings_context()), status_code


@profile_bp.route('/')
@login_required
def index():
    """View own profile - Optimized with reduced database queries"""
    cache_key = f'profile_index_{current_user.id}'
    cached = cache.get(cache_key)
    if cached:
        return render_template(
            'profile/index.html',
            user=current_user,
            seller_rating=cached.get('seller_rating'),
            notif_count=cached.get('notif_count'),
            latest_notifications=cached.get('latest_notifications', []),
            sales_unread_count=cached.get('sales_unread_count'),
            chat_unread_count=cached.get('chat_unread_count'),
            seller_request_summary=cached.get('seller_request_summary'),
            seller_plans=SELLER_PLANS
        )

    # Combined query for seller rating (if seller)
    seller_rating = None
    if current_user.is_seller:
        rating_result = db.session.query(
            func.coalesce(func.avg(SellerRating.rating), 0),
            func.count(SellerRating.id)
        ).filter(SellerRating.seller_id == current_user.id).first()
        seller_rating = {'avg': float(rating_result[0] or 0), 'count': int(rating_result[1] or 0)}

    # Get user notifications - simple separate queries
    user_notifications = UserNotification.query.filter_by(user_id=current_user.id)\
        .order_by(UserNotification.created_at.desc()).limit(5).all()
    
    # Get seller notifications
    seller_notifications = SellerNotification.query.filter_by(seller_id=current_user.id)\
        .order_by(SellerNotification.created_at.desc()).limit(5).all()
    
    # Combine notifications
    latest_notifications = []
    for n in user_notifications:
        latest_notifications.append({
            'kind': 'user',
            'row': n,
            'created_at': n.created_at,
            'message': n.message
        })
    for n in seller_notifications:
        latest_notifications.append({
            'kind': 'seller',
            'row': n,
            'created_at': n.created_at,
            'message': n.message
        })
    latest_notifications.sort(key=lambda x: x['created_at'] or datetime.min, reverse=True)
    latest_notifications = latest_notifications[:5]

    # Combined unread count - single query
    user_unread = db.session.query(func.count(UserNotification.id))\
        .filter(UserNotification.user_id == current_user.id, UserNotification.read_at.is_(None)).scalar() or 0
    seller_unread = db.session.query(func.count(SellerNotification.id))\
        .filter(SellerNotification.seller_id == current_user.id, SellerNotification.is_read.is_(False)).scalar() or 0
    notif_count = user_unread + seller_unread

    # Chat unread count - single optimized query
    chat_unread_count = db.session.query(func.count(SellerChatMessage.id))\
        .join(SellerChatConversation, SellerChatConversation.id == SellerChatMessage.conversation_id)\
        .filter(
            or_(
                SellerChatConversation.buyer_id == current_user.id,
                SellerChatConversation.seller_id == current_user.id
            ),
            SellerChatMessage.sender_id != current_user.id,
            SellerChatMessage.is_read.is_(False)
        ).scalar() or 0

    # Sales unread count
    sales_unread_count = 0
    if current_user.can_sell and not current_user.is_admin():
        last_seen = current_user.seller_sales_seen_at or datetime(1970, 1, 1)
        sales_unread_count = db.session.query(func.count(MerchOrder.id))\
            .join(Product, Product.id == MerchOrder.product_id)\
            .filter(Product.seller_id == current_user.id)\
            .filter(MerchOrder.purchased_at.isnot(None))\
            .filter(MerchOrder.purchased_at > last_seen).scalar() or 0

    # Seller request summary
    latest_request = _latest_seller_request_for_current_user()
    seller_request_summary = None
    if latest_request:
        seller_request_summary = {
            'status': latest_request.status,
            'plan_key': latest_request.plan_key,
            'plan_cost': latest_request.plan_cost,
            'created_at': latest_request.created_at,
            'reviewed_at': latest_request.reviewed_at,
        }

    # Cache for 30 seconds (reduced for fresher data)
    # Cache only simple data types to avoid serialization issues
    cache.set(cache_key, {
        'seller_rating': seller_rating,
        'notif_count': notif_count,
        'latest_notifications': latest_notifications[:5],
        'sales_unread_count': sales_unread_count,
        'chat_unread_count': chat_unread_count,
        'seller_request_summary': seller_request_summary
    }, timeout=30)

    return render_template(
        'profile/index.html',
        user=current_user,
        seller_rating=seller_rating,
        notif_count=notif_count,
        latest_notifications=latest_notifications[:5],
        sales_unread_count=sales_unread_count,
        chat_unread_count=chat_unread_count,
        seller_request_summary=seller_request_summary,
        seller_plans=SELLER_PLANS
    )


@profile_bp.route('/seller-hub')
@login_required
def seller_hub():
    latest_request = _latest_seller_request_for_current_user()
    seller_request_summary = None
    if latest_request:
        seller_request_summary = {
            'status': latest_request.status,
            'plan_key': latest_request.plan_key,
            'plan_cost': latest_request.plan_cost,
            'created_at': latest_request.created_at,
            'reviewed_at': latest_request.reviewed_at,
        }

    return render_template(
        'profile/seller_hub.html',
        seller_request=latest_request,
        seller_request_summary=seller_request_summary,
        seller_plans=SELLER_PLANS,
    )


@profile_bp.route('/<username>')

@login_required
def view(username):
    """View user profile"""
    if username.lower() == current_user.username.lower():
        user = current_user
        stats = UserService.get_user_stats(current_user.id)
        return render_template('profile/view.html', profile_user=user, stats=stats)

    cache_key = f'profile_view_{username}'
    cached_data = cache.get(cache_key)

    if cached_data is None:
        user = User.query.filter_by(username=username).first_or_404()
        stats = UserService.get_user_stats(user.id)
        cached_data = {'user': user, 'stats': stats}
        cache.set(cache_key, cached_data, timeout=120)

    return render_template('profile/view.html', profile_user=cached_data['user'], stats=cached_data['stats'])


@profile_bp.route('/edit', methods=['GET', 'POST'])

@login_required
def edit():
    """Edit profile"""
    if request.method == 'POST':
        bio = request.form.get('bio', '').strip()

        # Handle profile picture upload
        profile_pic = request.files.get('profile_pic')
        profile_pic_path = current_user.profile_pic
        seller_cover = request.files.get('seller_cover_photo')
        seller_cover_path = current_user.seller_cover_photo

        try:
            if profile_pic and profile_pic.filename:
                profile_pic_path = save_uploaded_image_optimized(profile_pic, 'profiles')

            if current_user.is_seller and seller_cover and seller_cover.filename:
                seller_cover_path = save_uploaded_image_optimized(seller_cover, 'profiles')
        except ValueError as exc:
            flash(str(exc), 'error')
            return render_template('profile/edit.html')

        # Update user
        current_user.bio = bio
        if profile_pic_path:
            current_user.profile_pic = profile_pic_path
        if current_user.is_seller:
            current_user.seller_cover_photo = seller_cover_path or ''

        db.session.commit()

        # Invalidate profile cache
        cache.delete(f'profile_view_{current_user.username}')
        cache.delete(f'profile_index_{current_user.id}')

        flash('Profile updated successfully!', 'success')
        return redirect(url_for('profile.index'))

    return render_template('profile/edit.html')


@profile_bp.route('/settings', methods=['GET', 'POST'])

@login_required
def settings():
    """User settings"""
    if request.method == 'POST':
        action = (request.form.get('action') or '').strip()

        # Change password
        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        if action == 'change_password' or any([current_password, new_password, confirm_password]):
            if not all([current_password, new_password, confirm_password]):
                flash('Please complete all password fields', 'error')
                return _render_settings()
            if not current_user.check_password(current_password):
                flash('Current password is incorrect', 'error')
                return _render_settings()

            if new_password != confirm_password:
                flash('New passwords do not match', 'error')
                return _render_settings()

            try:
                validate_password(new_password)
            except ValidationError as exc:
                flash(str(exc), 'error')
                return _render_settings()

            if UserService.is_password_reused(current_user, new_password):
                flash('Please choose a password you have not used recently.', 'error')
                return _render_settings()

            current_user.set_password(new_password)
            current_user.password_changed_at = utc_now()
            UserService.record_password_history(current_user)
            revoked_count = SessionService.revoke_other_sessions(current_user)
            SessionService.record_auth_event(
                'password_changed',
                user=current_user,
                status='success',
                details=f'other_sessions_revoked={revoked_count}',
            )
            db.session.commit()
            rotate_session_identifier()
            message = 'Password changed successfully!'
            if revoked_count:
                message += f' Signed out {revoked_count} other device{"s" if revoked_count != 1 else ""}.'
            flash(message, 'success')
            return redirect(url_for('profile.settings'))

        # Update account details
        email = request.form.get('email', '').strip()
        username = request.form.get('username', '').strip()
        email_changed = False
        username_changed = False

        if email:
            try:
                email = validate_email(email)
            except ValidationError as exc:
                flash(str(exc), 'error')
                return _render_settings()

            existing = User.query.filter(func.lower(User.email) == email.lower()).first()
            if existing and existing.id != current_user.id:
                flash('Email already in use', 'error')
                return _render_settings()

        if username:
            try:
                username = validate_username(username)
            except ValidationError as exc:
                flash(str(exc), 'error')
                return _render_settings()

            existing = User.query.filter(func.lower(User.username) == username.lower()).first()
            if existing and existing.id != current_user.id:
                flash('Username already taken', 'error')
                return _render_settings()

        if email and email.lower() != (current_user.email or '').lower():
            current_user.email = email
            current_user.email_verified_at = None
            if current_user.two_factor_enabled:
                current_user.two_factor_enabled = False
                flash('Two-factor login was disabled until the new email is verified.', 'info')
            email_changed = True

        if username and username.lower() != current_user.username.lower():
            cache.delete(f'profile_view_{current_user.username}')
            username_changed = True
            current_user.username = username

        if email_changed or username_changed:
            SessionService.record_auth_event(
                'account_settings_updated',
                user=current_user,
                status='info',
                details=f'email_changed={email_changed},username_changed={username_changed}',
            )
            db.session.commit()

            cache.delete(f'profile_view_{current_user.username}')
            cache.delete(f'profile_index_{current_user.id}')

            if email_changed and current_user.email:
                try:
                    OTPService.create_otp(user=current_user, purpose=OTPService.PURPOSE_EMAIL_VERIFY)
                    flash('Email updated. We sent a new verification code.', 'success')
                except ValueError as exc:
                    flash(f'Email updated, but verification email could not be sent yet: {exc}', 'warning')
            elif username_changed:
                flash('Username updated successfully!', 'success')
            else:
                flash('Account updated successfully!', 'success')
        else:
            flash('No account changes were made.', 'info')

        return redirect(url_for('profile.settings'))

    return _render_settings()


@profile_bp.route('/settings/security/preferences', methods=['POST'])
@login_required
def update_security_preferences():
    """Update account security preferences."""
    wants_2fa = request.form.get('two_factor_enabled') == 'on'
    wants_alerts = request.form.get('security_alerts_enabled') == 'on'

    if wants_2fa and (not current_user.email or not current_user.email_verified_at):
        flash('Verify your email address before enabling login security codes.', 'error')
        return redirect(url_for('profile.settings'))

    current_user.two_factor_enabled = wants_2fa
    current_user.security_alerts_enabled = wants_alerts
    SessionService.record_auth_event(
        'security_preferences_updated',
        user=current_user,
        status='info',
        details=f'two_factor={wants_2fa},alerts={wants_alerts}',
    )
    db.session.commit()
    flash('Security preferences updated.', 'success')
    return redirect(url_for('profile.settings') + '#security-preferences')


@profile_bp.route('/settings/security/email/send', methods=['POST'])
@login_required
def send_email_verification():
    """Send a verification code to the current account email."""
    if not current_user.email:
        flash('Add an email address first.', 'error')
        return redirect(url_for('profile.settings'))
    if current_user.email_verified_at:
        flash('Your email is already verified.', 'info')
        return redirect(url_for('profile.settings'))

    allowed, retry_after = consume_action_quota(
        'email_verify_send',
        limit=3,
        window_seconds=600,
        subject=current_user.email,
    )
    if not allowed:
        flash(f'Please wait about {retry_after} seconds before requesting another verification code.', 'error')
        return redirect(url_for('profile.settings') + '#email-verification')

    try:
        OTPService.create_otp(user=current_user, purpose=OTPService.PURPOSE_EMAIL_VERIFY)
        SessionService.record_auth_event('email_verification_sent', user=current_user, status='info')
        db.session.commit()
        flash('Verification code sent to your email.', 'success')
    except ValueError as exc:
        flash(str(exc), 'error')
    return redirect(url_for('profile.settings') + '#email-verification')


@profile_bp.route('/settings/security/email/verify', methods=['POST'])
@login_required
def verify_email_otp():
    """Verify the current user's email with an OTP code."""
    if not current_user.email:
        flash('Add an email address first.', 'error')
        return redirect(url_for('profile.settings'))

    allowed, retry_after = consume_action_quota(
        'email_verify_check',
        limit=6,
        window_seconds=900,
        subject=current_user.email,
    )
    if not allowed:
        flash(f'Too many verification attempts. Please wait about {retry_after} seconds and try again.', 'error')
        return redirect(url_for('profile.settings') + '#email-verification')

    otp_code = (request.form.get('otp_code') or '').strip()
    verified, message = OTPService.verify_otp(
        user=current_user,
        purpose=OTPService.PURPOSE_EMAIL_VERIFY,
        code=otp_code,
    )
    if not verified:
        SessionService.record_auth_event('otp_failure', user=current_user, status='warning', details='email_verify')
        db.session.commit()
        flash(message, 'error')
        return redirect(url_for('profile.settings') + '#email-verification')

    current_user.email_verified_at = utc_now()
    SessionService.record_auth_event('email_verified', user=current_user, status='success')
    db.session.commit()
    flash('Email verified successfully.', 'success')
    return redirect(url_for('profile.settings') + '#email-verification')


@profile_bp.route('/settings/security/sessions/<int:session_id>/revoke', methods=['POST'])
@login_required
def revoke_session(session_id):
    """Revoke one of the current user's other sessions."""
    current_session = SessionService.get_current_session()
    if current_session and current_session.id == session_id:
        flash('Use the normal logout button to sign out this device.', 'info')
        return redirect(url_for('profile.settings') + '#active-sessions')

    if not SessionService.revoke_user_session(current_user, session_id):
        flash('That session could not be revoked.', 'error')
        return redirect(url_for('profile.settings') + '#active-sessions')

    flash('Session revoked successfully.', 'success')
    return redirect(url_for('profile.settings') + '#active-sessions')


@profile_bp.route('/settings/security/sessions/logout-others', methods=['POST'])
@login_required
def logout_other_devices():
    """Logout all devices except the current one."""
    count = SessionService.revoke_other_sessions(current_user)
    if count:
        flash(f'Signed out {count} other device{"s" if count != 1 else ""}.', 'success')
    else:
        flash('No other active devices were found.', 'info')
    return redirect(url_for('profile.settings') + '#active-sessions')


@profile_bp.route('/notifications')
@login_required
def notifications():
    """User notifications."""
    params = PaginationService.get_page_args(request.args.get('page', 1, type=int), 20)
    user_rows = UserNotification.query.filter_by(user_id=current_user.id)\
        .order_by(UserNotification.created_at.desc(), UserNotification.id.desc())\
        .limit(params.per_page).all()
    seller_rows = SellerNotification.query.filter_by(seller_id=current_user.id)\
        .order_by(SellerNotification.created_at.desc(), SellerNotification.id.desc())\
        .limit(params.per_page).all()

    rows = sorted(
        [{'kind': 'user', 'row': n, 'created_at': n.created_at} for n in user_rows] +
        [{'kind': 'seller', 'row': n, 'created_at': n.created_at} for n in seller_rows],
        key=lambda item: item['created_at'] or datetime.min,
        reverse=True
    )

    unread_count = UserNotification.query.filter_by(user_id=current_user.id, read_at=None).count()
    seller_unread_count = SellerNotification.query.filter_by(seller_id=current_user.id, is_read=False).count()
    if unread_count or seller_unread_count:
        now = utc_now()
        UserNotification.query.filter_by(user_id=current_user.id, read_at=None).update({'read_at': now})
        SellerNotification.query.filter_by(seller_id=current_user.id, is_read=False).update({'is_read': True})
        db.session.commit()
        cache.delete(f'profile_index_{current_user.id}')
    return render_template('profile/notifications.html', notifications=rows)


@profile_bp.route('/seller-request', methods=['POST'])
@login_required
def seller_request():
    """Submit seller access request."""
    existing_pending = SellerRequest.query.filter_by(
        user_id=current_user.id,
        status='pending'
    ).first()
    if existing_pending:
        flash('You already have a pending seller request.', 'error')
        return redirect(url_for('profile.seller_hub'))

    if current_user.is_seller:
        flash('Your seller access is already approved.', 'info')
        return redirect(url_for('profile.seller_hub'))

    real_name = (request.form.get('real_name') or '').strip()
    country = (request.form.get('country') or '').strip()
    city = (request.form.get('city') or '').strip()
    phone = (request.form.get('phone') or '').strip()
    product_description = (request.form.get('product_description') or '').strip()
    location_text = (request.form.get('location_text') or '').strip()
    location_lat = request.form.get('location_lat', type=float)
    location_lng = request.form.get('location_lng', type=float)
    plan_key = (request.form.get('plan') or '').strip()
    plan = SELLER_PLANS.get(plan_key)

    id_front = request.files.get('id_front')
    id_back = request.files.get('id_back')

    if not all([real_name, country, city, phone, product_description]):
        flash('All seller request fields are required.', 'error')
        return redirect(url_for('profile.seller_hub'))

    if not plan:
        flash('Please choose a seller plan.', 'error')
        return redirect(url_for('profile.seller_hub'))

    if not id_front or not id_front.filename or not id_back or not id_back.filename:
        flash('ID card front and back images are required.', 'error')
        return redirect(url_for('profile.seller_hub'))

    try:
        id_front_path = save_uploaded_image_optimized(id_front, 'seller_ids')
        id_back_path = save_uploaded_image_optimized(id_back, 'seller_ids')
    except ValueError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('profile.seller_hub'))

    cost = int(plan['cost'])
    try:
        WalletService.debit_user(
            user_id=current_user.id,
            amount=cost,
            transaction_type='seller_request_fee',
            details=plan_key,
        )

        new_request = SellerRequest(
            user_id=current_user.id,
            real_name=real_name,
            country=country,
            city=city,
            phone=phone,
            product_description=product_description,
            id_front_path=id_front_path,
            id_back_path=id_back_path,
            location_text=location_text or None,
            location_lat=location_lat,
            location_lng=location_lng,
            plan_key=plan_key,
            plan_months=int(plan['months']),
            plan_cost=cost,
            status='pending'
        )
        db.session.add(new_request)
        db.session.flush()
        WalletService.record_transaction(
            user_id=current_user.id,
            amount=0,
            transaction_type='seller_request_created',
            status='pending',
            reference_type='seller_request',
            reference_id=new_request.id,
            details=plan_key,
        )
        db.session.commit()
    except ValidationError:
        db.session.rollback()
        flash(f'Insufficient TNNO. Need {cost:,}, you have {int(current_user.coins):,}.', 'error')
        return redirect(url_for('profile.seller_hub'))

    cache.delete(f'profile_index_{current_user.id}')
    flash('Seller request submitted. Plan fee charged. Admin will review it soon.', 'success')
    return redirect(url_for('profile.seller_hub'))


@profile_bp.route('/seller-plan', methods=['POST'])
@login_required
def seller_plan():
    """Purchase or renew seller subscription."""
    if not current_user.is_seller and not current_user.is_admin():
        flash('Seller access must be approved before purchasing a plan.', 'error')
        return redirect(url_for('profile.seller_hub'))

    plan_key = (request.form.get('plan') or '').strip()
    plan = SELLER_PLANS.get(plan_key)
    if not plan:
        flash('Invalid seller plan selected.', 'error')
        return redirect(get_safe_redirect_target(request.referrer, 'profile.seller_hub'))

    cost = int(plan['cost'])
    try:
        user = WalletService.debit_user(
            user_id=current_user.id,
            amount=cost,
            transaction_type='seller_plan_purchase',
            details=plan_key,
        )
        user.is_seller = True
        user.seller_expires_at = SellerService.compute_new_expiry(
            user.seller_expires_at,
            plan['months']
        )
        db.session.commit()
    except ValidationError:
        db.session.rollback()
        flash(f'Insufficient TNNO. Need {cost:,}, you have {int(current_user.coins):,}.', 'error')
        return redirect(get_safe_redirect_target(request.referrer, 'profile.seller_hub'))

    cache.delete(f'profile_index_{current_user.id}')
    flash('Seller plan activated successfully!', 'success')
    return redirect(get_safe_redirect_target(request.referrer, 'profile.seller_hub'))


@profile_bp.route('/delete-account', methods=['POST'])

@login_required
def delete_account():
    """Delete user account"""
    from flask_login import logout_user

    user_id = current_user.id
    user = db.session.get(User, user_id)
    delete_password = request.form.get('delete_password', '')
    confirmation = (request.form.get('delete_confirmation') or '').strip().upper()

    if not user:
        flash('User not found', 'error')
        return redirect(url_for('profile.index'))

    if confirmation != 'DELETE':
        flash('Type DELETE to confirm account deletion', 'error')
        return redirect(url_for('profile.settings'))

    if not user.check_password(delete_password):
        flash('Current password is required to delete your account', 'error')
        return redirect(url_for('profile.settings'))

    # Delete user from database
    SessionService.revoke_current_session(reason='account_deleted')
    db.session.delete(user)
    db.session.commit()

    # Logout the user
    logout_user()
    rotate_session_identifier(clear_session=True)
    flash('Account deleted successfully', 'success')
    response = redirect(url_for('auth.login'))
    clear_auth_cookies(response)
    return response


@profile_bp.route('/leaderboard')
@login_required
def leaderboard():
    """View leaderboard - requires login for security"""
    tab = (request.args.get('tab') or 'users').lower()
    admin_username = (current_app.config.get('ADMIN_USER', 'admin') or 'admin').lower()

    if tab == 'sellers':
        cache_key = f'leaderboard_sellers_user_{current_user.id}'
        cached = cache.get(cache_key)
        if cached is None:
            sales_subq = db.session.query(
                Product.seller_id.label('seller_id'),
                db.func.coalesce(db.func.sum(MerchOrder.total_price), 0).label('total_sales')
            ).join(Product, Product.id == MerchOrder.product_id)\
             .filter(MerchOrder.status == 'completed')\
             .group_by(Product.seller_id)\
             .subquery()

            ratings_subq = db.session.query(
                SellerRating.seller_id.label('seller_id'),
                db.func.coalesce(db.func.avg(SellerRating.rating), 0).label('avg_rating'),
                db.func.count(SellerRating.id).label('rating_count')
            ).group_by(SellerRating.seller_id)\
             .subquery()

            rows = db.session.query(
                User,
                sales_subq.c.total_sales,
                ratings_subq.c.avg_rating,
                ratings_subq.c.rating_count
            ).join(sales_subq, sales_subq.c.seller_id == User.id)\
             .filter(User.role != 'admin')\
             .filter(db.func.lower(User.username) != admin_username)\
             .outerjoin(ratings_subq, ratings_subq.c.seller_id == User.id)\
             .order_by(sales_subq.c.total_sales.desc())\
             .limit(50)\
             .all()

            cached = {'rows': rows}
            cache.set(cache_key, cached, timeout=300)

        return render_template('profile/leaderboard.html',
                               tab='sellers',
                               seller_rows=cached['rows'])

    # Default: top users by coins
    cache_key = f'leaderboard_user_{current_user.id}'
    cached = cache.get(cache_key)

    if cached is None:
        leaders = UserService.get_leaderboard(limit=50)
        # Rank = users with strictly higher coin balance + 1
        higher_count = db.session.query(db.func.count(User.id))\
            .filter(User.role != 'admin')\
            .filter(db.func.lower(User.username) != admin_username)\
            .filter(User.coins > (current_user.coins or 0))\
            .scalar() or 0
        user_rank = higher_count + 1
        cached = {'leaders': leaders, 'user_rank': user_rank}
        cache.set(cache_key, cached, timeout=300)

    return render_template('profile/leaderboard.html',
                           tab='users',
                           leaders=cached['leaders'],
                           user_rank=cached['user_rank'])
