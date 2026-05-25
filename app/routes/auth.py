"""
Auth Routes
User authentication (signup, login, logout)
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, session, current_app, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import func
from app.datetime_utils import utc_now
from app.extensions import db
from app.models import User
from app.security import (
    clear_auth_cookies,
    clear_auth_failures,
    consume_action_quota,
    get_safe_redirect_target,
    is_auth_throttled,
    register_auth_failure,
    rotate_session_identifier,
)
from app.services import UserService
from app.services.otp_service import OTPService
from app.services.session_service import SessionService
from app.validators import ValidationError, validate_password, validate_username

auth_bp = Blueprint('auth', __name__)


def _find_user_by_identifier(identifier: str) -> User | None:
    raw = (identifier or '').strip()
    if not raw:
        return None
    if '@' in raw:
        return User.query.filter(func.lower(User.email) == raw.lower()).first()
    return User.query.filter(func.lower(User.username) == raw.lower()).first()


def _clear_pending_login_state() -> None:
    session.pop('pending_login_user_id', None)
    session.pop('pending_login_remember', None)
    session.pop('pending_login_next', None)


def _complete_login(user: User, *, remember: bool, next_page: str):
    rotate_session_identifier(clear_session=True)
    login_user(user, remember=remember)
    session.permanent = remember
    SessionService.create_authenticated_session(user, remember=remember)
    clear_auth_failures(user.username)
    flash('Login successful!', 'success')
    return redirect(next_page)


@auth_bp.route('/')
def index():
    """Home page - redirect to dashboard or login"""
    if current_user.is_authenticated:
        return redirect(url_for('missions.index'))
    return redirect(url_for('auth.login'))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """User login page - PRO APP STYLE with remember me"""
    if current_user.is_authenticated:
        return redirect(url_for('missions.index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember_raw = request.form.get('remember', 'false')
        remember = str(remember_raw).lower() in ('1', 'true', 'on', 'yes')

        if not username or not password:
            flash('Please enter username and password', 'error')
            return render_template('auth/login.html')

        allowed, retry_after = consume_action_quota(
            'login_attempt',
            limit=12,
            window_seconds=60,
            subject=username,
        )
        if not allowed:
            SessionService.record_auth_event('login_rate_limited', username=username, status='warning', details='per_minute_quota')
            db.session.commit()
            flash(f'Too many login attempts. Please wait about {retry_after} seconds and try again.', 'error')
            return render_template('auth/login.html'), 429

        if is_auth_throttled(username):
            SessionService.record_auth_event('login_throttled', username=username, status='warning', details='lockout_active')
            db.session.commit()
            flash('Too many failed login attempts. Please try again later.', 'error')
            return render_template('auth/login.html'), 429

        user, message = UserService.authenticate_user(username, password)
        
        if user:
            next_page = get_safe_redirect_target(request.args.get('next'), 'missions.index')
            if user.two_factor_enabled and user.email and user.email_verified_at:
                try:
                    OTPService.create_otp(user=user, purpose=OTPService.PURPOSE_LOGIN)
                except ValueError as exc:
                    flash(str(exc), 'error')
                    return render_template('auth/login.html'), 429
                session['pending_login_user_id'] = user.id
                session['pending_login_remember'] = '1' if remember else '0'
                session['pending_login_next'] = next_page
                SessionService.record_auth_event('login_otp_challenge', user=user, status='info')
                db.session.commit()
                flash('Enter the security code sent to your email.', 'info')
                return redirect(url_for('auth.login_otp'))
            return _complete_login(user, remember=remember, next_page=next_page)
        else:
            register_auth_failure(username)
            SessionService.record_auth_event('login_failure', username=username, status='warning', details='invalid_credentials')
            db.session.commit()
            flash(message, 'error')

    return render_template('auth/login.html')


@auth_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    """User registration page"""
    if current_user.is_authenticated:
        return redirect(url_for('missions.index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        email = request.form.get('email', '').strip()

        try:
            validate_username(username)
            validate_password(password)
        except ValidationError as exc:
            flash(str(exc), 'error')
            return render_template('auth/signup.html')

        if password != confirm_password:
            flash('Passwords do not match', 'error')
            return render_template('auth/signup.html')

        # Create user
        user, message = UserService.create_user(username, password, email if email else None)

        if user:
            user.password_changed_at = utc_now()
            if user.email:
                try:
                    OTPService.create_otp(user=user, purpose=OTPService.PURPOSE_EMAIL_VERIFY)
                    flash('Registration successful! Please login. We also sent an email verification code.', 'success')
                except ValueError:
                    flash('Registration successful! Please login.', 'success')
            else:
                flash('Registration successful! Please login.', 'success')
            SessionService.record_auth_event('signup_success', user=user, status='success')
            db.session.commit()
            return redirect(url_for('auth.login'))
        else:
            flash(message, 'error')

    return render_template('auth/signup.html')


@auth_bp.route('/about-app')
def about_app():
    """About app page with feature guide and rules."""
    return render_template('auth/about_app.html')


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    """User logout"""
    SessionService.revoke_current_session(reason='logout')
    logout_user()
    rotate_session_identifier(clear_session=True)
    flash('You have been logged out', 'info')
    response = redirect(url_for('auth.login'))
    clear_auth_cookies(response)
    return response


@auth_bp.route('/logout', methods=['GET'])
def logout_get():
    """Prevent accidental logout via prefetch/crawlers; require POST for sign-out."""
    if current_user.is_authenticated:
        flash('Use the Logout button to sign out.', 'info')
        return redirect(url_for('missions.index'))
    return redirect(url_for('auth.login'))


@auth_bp.route('/login/otp', methods=['GET', 'POST'])
def login_otp():
    """Second step for optional email OTP / 2FA login."""
    pending_user_id = session.get('pending_login_user_id')
    if not pending_user_id:
        return redirect(url_for('auth.login'))

    user = db.session.get(User, int(pending_user_id))
    if not user:
        _clear_pending_login_state()
        flash('Please sign in again.', 'error')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        action = (request.form.get('action') or 'verify').strip().lower()
        if action == 'resend':
            allowed, retry_after = consume_action_quota(
                'login_otp_resend',
                limit=3,
                window_seconds=600,
                subject=user.email or user.username,
            )
            if not allowed:
                flash(f'Please wait about {retry_after} seconds before requesting another security code.', 'error')
                return render_template('auth/login_otp.html', pending_user=user)
            try:
                OTPService.create_otp(user=user, purpose=OTPService.PURPOSE_LOGIN)
                SessionService.record_auth_event('otp_resent', user=user, status='info', details='login_otp')
                db.session.commit()
                flash('A new security code was sent.', 'info')
            except ValueError as exc:
                flash(str(exc), 'error')
            return render_template('auth/login_otp.html', pending_user=user)

        allowed, retry_after = consume_action_quota(
            'login_otp_verify',
            limit=8,
            window_seconds=600,
            subject=user.email or user.username,
        )
        if not allowed:
            SessionService.record_auth_event('otp_rate_limited', user=user, status='warning', details='login_otp')
            db.session.commit()
            flash(f'Too many code attempts. Please wait about {retry_after} seconds and try again.', 'error')
            return render_template('auth/login_otp.html', pending_user=user), 429

        otp_code = (request.form.get('otp_code') or '').strip()
        verified, message = OTPService.verify_otp(user=user, purpose=OTPService.PURPOSE_LOGIN, code=otp_code)
        if not verified:
            register_auth_failure(user.username)
            SessionService.record_auth_event('otp_failure', user=user, status='warning', details='login_otp')
            db.session.commit()
            flash(message, 'error')
            return render_template('auth/login_otp.html', pending_user=user)

        remember = session.get('pending_login_remember') == '1'
        next_page = session.get('pending_login_next') or url_for('missions.index')
        _clear_pending_login_state()
        SessionService.record_auth_event('otp_success', user=user, status='success', details='login_otp')
        db.session.commit()
        return _complete_login(user, remember=remember, next_page=next_page)

    return render_template('auth/login_otp.html', pending_user=user)


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Request a password reset OTP."""
    if current_user.is_authenticated:
        return redirect(url_for('missions.index'))

    if request.method == 'POST':
        identifier = (request.form.get('identifier') or '').strip()
        allowed, retry_after = consume_action_quota(
            'password_reset_request',
            limit=3,
            window_seconds=900,
            subject=identifier,
        )
        if not allowed:
            flash(f'Too many reset requests. Please wait about {retry_after} seconds and try again.', 'error')
            return render_template('auth/forgot_password.html'), 429

        user = _find_user_by_identifier(identifier)
        if user and user.email:
            try:
                OTPService.create_otp(user=user, purpose=OTPService.PURPOSE_PASSWORD_RESET)
                SessionService.record_auth_event('password_reset_requested', user=user, status='info')
                db.session.commit()
            except ValueError as exc:
                flash(str(exc), 'error')
                return render_template('auth/forgot_password.html')

        flash('If the account exists, a password reset code was sent.', 'info')
        return redirect(url_for('auth.reset_password'))

    return render_template('auth/forgot_password.html')


@auth_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    """Complete password reset using an emailed OTP."""
    if current_user.is_authenticated:
        return redirect(url_for('missions.index'))

    if request.method == 'POST':
        identifier = (request.form.get('identifier') or '').strip()
        otp_code = (request.form.get('otp_code') or '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        user = _find_user_by_identifier(identifier)

        if not user or not user.email:
            flash('Unable to reset password with the supplied details.', 'error')
            return render_template('auth/reset_password.html')

        if password != confirm_password:
            flash('Passwords do not match', 'error')
            return render_template('auth/reset_password.html')

        try:
            validate_password(password)
        except ValidationError as exc:
            flash(str(exc), 'error')
            return render_template('auth/reset_password.html')

        if UserService.is_password_reused(user, password):
            flash('Please choose a password you have not used recently.', 'error')
            return render_template('auth/reset_password.html')

        allowed, retry_after = consume_action_quota(
            'password_reset_verify',
            limit=6,
            window_seconds=900,
            subject=user.email or user.username,
        )
        if not allowed:
            flash(f'Too many reset attempts. Please wait about {retry_after} seconds and try again.', 'error')
            return render_template('auth/reset_password.html'), 429

        verified, message = OTPService.verify_otp(user=user, purpose=OTPService.PURPOSE_PASSWORD_RESET, code=otp_code)
        if not verified:
            register_auth_failure(user.username)
            SessionService.record_auth_event('otp_failure', user=user, status='warning', details='password_reset')
            db.session.commit()
            flash(message, 'error')
            return render_template('auth/reset_password.html')

        user.set_password(password)
        user.password_changed_at = utc_now()
        UserService.record_password_history(user)
        SessionService.revoke_all_user_sessions(user)
        clear_auth_failures(user.username)
        SessionService.record_auth_event('password_reset_success', user=user, status='success')
        db.session.commit()
        flash('Password reset successful. Please sign in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/reset_password.html')


@auth_bp.route('/check_username', methods=['POST'])
def check_username():
    """Check if username is available"""
    username = request.form.get('username', '').strip()
    allowed, retry_after = consume_action_quota(
        'check_username',
        limit=30,
        window_seconds=60,
        subject=username,
    )
    if not allowed:
        return jsonify({'available': False, 'message': f'Please wait about {retry_after} seconds before checking again.'}), 429
    try:
        username = validate_username(username)
    except ValidationError as exc:
        return jsonify({'available': False, 'message': str(exc)})

    admin_username = current_app.config.get('ADMIN_USER', 'admin')
    if username.lower() in {admin_username.lower(), 'admin'}:
        return jsonify({'available': False, 'message': 'This username is reserved'})
    
    existing = UserService.get_user_by_username(username)
    if existing:
        return jsonify({'available': False, 'message': 'Your username is already taken'})
    else:
        return jsonify({'available': True, 'message': 'Username is available'})
