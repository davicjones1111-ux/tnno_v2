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
from app.validators import ValidationError, validate_email, validate_password, validate_username

auth_bp = Blueprint('auth', __name__)


def _find_user_by_identifier(identifier: str) -> User | None:
    raw = (identifier or '').strip()
    if not raw:
        return None
    if '@' in raw:
        return User.query.filter(func.lower(User.email) == raw.lower()).first()
    return User.query.filter(func.lower(User.username) == raw.lower()).first()


def _auth_request_data():
    if request.is_json:
        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            return payload
    return request.form


def _wants_json_response() -> bool:
    return request.is_json or request.accept_mimetypes.best == 'application/json'


def _json_response(ok: bool, message: str, *, status_code: int = 200, **extra):
    payload = {'ok': ok, 'message': message}
    payload.update(extra)
    return jsonify(payload), status_code


def _clear_pending_login_state() -> None:
    session.pop('pending_login_user_id', None)
    session.pop('pending_login_remember', None)
    session.pop('pending_login_next', None)


def _clear_pending_email_verification_state() -> None:
    session.pop('pending_email_verification_user_id', None)
    session.pop('pending_email_verification_source', None)
    session.pop('pending_email_verification_next', None)


def _set_pending_email_verification_state(user: User, *, source: str, next_page: str) -> None:
    session['pending_email_verification_user_id'] = user.id
    session['pending_email_verification_source'] = source
    session['pending_email_verification_next'] = next_page


def _finalize_login(user: User, *, remember: bool, next_page: str) -> None:
    rotate_session_identifier(clear_session=True)
    login_user(user, remember=remember)
    session.permanent = remember
    SessionService.create_authenticated_session(user, remember=remember)
    clear_auth_failures(user.username)


def _complete_login(user: User, *, remember: bool, next_page: str):
    _finalize_login(user, remember=remember, next_page=next_page)
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
        data = _auth_request_data()
        identifier = (data.get('identifier') or data.get('username') or data.get('email') or '').strip()
        password = data.get('password', '')
        remember_raw = data.get('remember', 'false')
        remember = str(remember_raw).lower() in ('1', 'true', 'on', 'yes')

        if not identifier or not password:
            if _wants_json_response():
                return _json_response(False, 'Please enter a username or email and password.', status_code=400)
            flash('Please enter a username or email and password', 'error')
            return render_template('auth/login.html')

        allowed, retry_after = consume_action_quota(
            'login_attempt',
            limit=12,
            window_seconds=60,
            subject=identifier,
        )
        if not allowed:
            SessionService.record_auth_event('login_rate_limited', username=identifier, status='warning', details='per_minute_quota')
            db.session.commit()
            message = f'Too many login attempts. Please wait about {retry_after} seconds and try again.'
            if _wants_json_response():
                return _json_response(False, message, status_code=429)
            flash(message, 'error')
            return render_template('auth/login.html'), 429

        if is_auth_throttled(identifier):
            SessionService.record_auth_event('login_throttled', username=identifier, status='warning', details='lockout_active')
            db.session.commit()
            message = 'Too many failed login attempts. Please try again later.'
            if _wants_json_response():
                return _json_response(False, message, status_code=429)
            flash(message, 'error')
            return render_template('auth/login.html'), 429

        user, message = UserService.authenticate_user(identifier, password)
        
        if user:
            next_page = get_safe_redirect_target(request.args.get('next'), 'missions.index')
            if user.email and not user.email_verified_at:
                login_retry_url = url_for('auth.login', next=request.args.get('next'))
                try:
                    OTPService.create_otp(user=user, purpose=OTPService.PURPOSE_EMAIL_VERIFY)
                except ValueError as exc:
                    _set_pending_email_verification_state(user, source='login', next_page=login_retry_url)
                    message = str(exc)
                    if _wants_json_response():
                        return _json_response(True, 'Please verify your email address to continue.', status_code=202, requires_email_verification=True, verify_url=url_for('auth.verify_email'), warning=message)
                    flash('Please verify your email address to continue.', 'info')
                    flash(message, 'warning')
                    return redirect(url_for('auth.verify_email'))
                _set_pending_email_verification_state(user, source='login', next_page=login_retry_url)
                SessionService.record_auth_event('login_email_verification_sent', user=user, status='info')
                db.session.commit()
                message = 'Please verify your email address to continue.'
                if _wants_json_response():
                    return _json_response(True, message, status_code=200, requires_email_verification=True, verify_url=url_for('auth.verify_email'))
                flash(message, 'info')
                return redirect(url_for('auth.verify_email'))
            if user.email:
                try:
                    OTPService.create_otp(user=user, purpose=OTPService.PURPOSE_LOGIN)
                except ValueError as exc:
                    if _wants_json_response():
                        return _json_response(False, str(exc), status_code=429)
                    flash(str(exc), 'error')
                    return render_template('auth/login.html'), 429
                session['pending_login_user_id'] = user.id
                session['pending_login_remember'] = '1' if remember else '0'
                session['pending_login_next'] = next_page
                SessionService.record_auth_event('login_otp_challenge', user=user, status='info')
                db.session.commit()
                message = 'Enter the security code sent to your email.'
                if _wants_json_response():
                    return _json_response(True, message, status_code=200, requires_otp=True, next_url=url_for('auth.login_otp'))
                flash(message, 'info')
                return redirect(url_for('auth.login_otp'))
            if _wants_json_response():
                _finalize_login(user, remember=remember, next_page=next_page)
                return _json_response(True, 'Login successful.', status_code=200, redirect_url=next_page)
            return _complete_login(user, remember=remember, next_page=next_page)
        else:
            register_auth_failure(identifier)
            SessionService.record_auth_event('login_failure', username=identifier, status='warning', details='invalid_credentials')
            db.session.commit()
            if _wants_json_response():
                return _json_response(False, message, status_code=401)
            flash(message, 'error')

    return render_template('auth/login.html')


@auth_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    """User registration page"""
    if current_user.is_authenticated:
        return redirect(url_for('missions.index'))

    if request.method == 'POST':
        data = _auth_request_data()
        username = (data.get('username') or '').strip()
        password = data.get('password', '')
        confirm_password = data.get('confirm_password', '')
        email = (data.get('email') or '').strip()

        try:
            validate_username(username)
            validate_password(password)
            if not email:
                raise ValidationError('Email is required')
            email = validate_email(email)
        except ValidationError as exc:
            if _wants_json_response():
                return _json_response(False, str(exc), status_code=400)
            flash(str(exc), 'error')
            return render_template('auth/signup.html')

        if password != confirm_password:
            if _wants_json_response():
                return _json_response(False, 'Passwords do not match', status_code=400)
            flash('Passwords do not match', 'error')
            return render_template('auth/signup.html')

        # Create user
        user, message = UserService.create_user(username, password, email)

        if user:
            user.password_changed_at = utc_now()
            try:
                OTPService.create_otp(user=user, purpose=OTPService.PURPOSE_EMAIL_VERIFY)
                _set_pending_email_verification_state(user, source='signup', next_page=url_for('auth.login'))
                SessionService.record_auth_event('signup_verification_sent', user=user, status='info')
                db.session.commit()
            except ValueError as exc:
                current_app.logger.warning(
                    'Signup verification email could not be sent for user=%s email=%s: %s',
                    user.username,
                    user.email,
                    exc,
                )
                if _wants_json_response():
                    return _json_response(
                        True,
                        'Account created. Check your email for a verification code.',
                        status_code=202,
                        requires_email_verification=True,
                        verify_url=url_for('auth.verify_email'),
                        warning=str(exc),
                    )
                _set_pending_email_verification_state(user, source='signup', next_page=url_for('auth.login'))
                flash('Account created. Check your email for a verification code.', 'success')
                flash('We could not send the verification code right now. Please resend it from the verification page.', 'warning')
                return redirect(url_for('auth.verify_email'))
            if _wants_json_response():
                return _json_response(
                    True,
                    'Account created. Check your email for a verification code.',
                    status_code=201,
                    requires_email_verification=True,
                    verify_url=url_for('auth.verify_email'),
                )
            flash('Account created. Check your email for a verification code.', 'success')
            return redirect(url_for('auth.verify_email'))
        else:
            if _wants_json_response():
                return _json_response(False, message, status_code=400)
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


@auth_bp.route('/verify-email', methods=['GET', 'POST'])
def verify_email():
    """Verify email during signup or when login is blocked for an unverified account."""
    pending_user_id = session.get('pending_email_verification_user_id')
    if not pending_user_id:
        return redirect(url_for('auth.login'))

    user = db.session.get(User, int(pending_user_id))
    if not user:
        _clear_pending_email_verification_state()
        flash('Please sign in again.', 'error')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        data = _auth_request_data()
        action = (data.get('action') or 'verify').strip().lower()
        if action == 'resend':
            allowed, retry_after = consume_action_quota(
                'email_verify_resend',
                limit=3,
                window_seconds=600,
                subject=user.email or user.username,
            )
            if not allowed:
                message = f'Please wait about {retry_after} seconds before requesting another verification code.'
                if _wants_json_response():
                    return _json_response(False, message, status_code=429)
                flash(message, 'error')
                return render_template('auth/verify_email.html', pending_user=user)
            try:
                OTPService.create_otp(user=user, purpose=OTPService.PURPOSE_EMAIL_VERIFY)
                SessionService.record_auth_event('email_verification_resent', user=user, status='info')
                db.session.commit()
                message = 'A new verification code was sent.'
                if _wants_json_response():
                    return _json_response(True, message, status_code=200)
                flash(message, 'info')
            except ValueError as exc:
                if _wants_json_response():
                    return _json_response(False, str(exc), status_code=429)
                flash(str(exc), 'error')
            return render_template('auth/verify_email.html', pending_user=user)

        allowed, retry_after = consume_action_quota(
            'email_verify_check',
            limit=8,
            window_seconds=600,
            subject=user.email or user.username,
        )
        if not allowed:
            message = f'Too many verification attempts. Please wait about {retry_after} seconds and try again.'
            if _wants_json_response():
                return _json_response(False, message, status_code=429)
            flash(message, 'error')
            return render_template('auth/verify_email.html', pending_user=user), 429

        otp_code = (data.get('otp_code') or '').strip()
        verified, message = OTPService.verify_otp(user=user, purpose=OTPService.PURPOSE_EMAIL_VERIFY, code=otp_code)
        if not verified:
            SessionService.record_auth_event('otp_failure', user=user, status='warning', details='email_verify')
            db.session.commit()
            if _wants_json_response():
                return _json_response(False, message, status_code=400)
            flash(message, 'error')
            return render_template('auth/verify_email.html', pending_user=user)

        source = session.get('pending_email_verification_source') or 'signup'
        next_page = session.get('pending_email_verification_next') or url_for('auth.login')
        user.email_verified_at = utc_now()
        _clear_pending_email_verification_state()
        SessionService.record_auth_event('email_verified', user=user, status='success', details=source)
        db.session.commit()
        if _wants_json_response():
            return _json_response(True, 'Email verified successfully.', status_code=200, verified=True)
        flash('Email verified successfully. You can now sign in.', 'success')
        if source == 'login':
            flash('Now complete the login step with your password.', 'info')
        return redirect(next_page)

    return render_template('auth/verify_email.html', pending_user=user)


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
        data = _auth_request_data()
        action = (data.get('action') or 'verify').strip().lower()
        if action == 'resend':
            allowed, retry_after = consume_action_quota(
                'login_otp_resend',
                limit=3,
                window_seconds=600,
                subject=user.email or user.username,
            )
            if not allowed:
                message = f'Please wait about {retry_after} seconds before requesting another security code.'
                if _wants_json_response():
                    return _json_response(False, message, status_code=429)
                flash(message, 'error')
                return render_template('auth/login_otp.html', pending_user=user)
            try:
                OTPService.create_otp(user=user, purpose=OTPService.PURPOSE_LOGIN)
                SessionService.record_auth_event('otp_resent', user=user, status='info', details='login_otp')
                db.session.commit()
                message = 'A new security code was sent.'
                if _wants_json_response():
                    return _json_response(True, message, status_code=200)
                flash(message, 'info')
            except ValueError as exc:
                if _wants_json_response():
                    return _json_response(False, str(exc), status_code=429)
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
            message = f'Too many code attempts. Please wait about {retry_after} seconds and try again.'
            if _wants_json_response():
                return _json_response(False, message, status_code=429)
            flash(message, 'error')
            return render_template('auth/login_otp.html', pending_user=user), 429

        otp_code = (data.get('otp_code') or '').strip()
        verified, message = OTPService.verify_otp(user=user, purpose=OTPService.PURPOSE_LOGIN, code=otp_code)
        if not verified:
            register_auth_failure(user.username)
            SessionService.record_auth_event('otp_failure', user=user, status='warning', details='login_otp')
            db.session.commit()
            if _wants_json_response():
                return _json_response(False, message, status_code=400)
            flash(message, 'error')
            return render_template('auth/login_otp.html', pending_user=user)

        remember = session.get('pending_login_remember') == '1'
        next_page = session.get('pending_login_next') or url_for('missions.index')
        _clear_pending_login_state()
        SessionService.record_auth_event('otp_success', user=user, status='success', details='login_otp')
        db.session.commit()
        if _wants_json_response():
            _finalize_login(user, remember=remember, next_page=next_page)
            return _json_response(True, 'Login successful.', status_code=200, redirect_url=next_page)
        return _complete_login(user, remember=remember, next_page=next_page)

    return render_template('auth/login_otp.html', pending_user=user)


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Request a password reset OTP."""
    if current_user.is_authenticated:
        return redirect(url_for('missions.index'))

    if request.method == 'POST':
        data = _auth_request_data()
        identifier = (data.get('identifier') or data.get('username') or data.get('email') or '').strip()
        if not identifier:
            message = 'Please enter your username or email.'
            if _wants_json_response():
                return _json_response(False, message, status_code=400)
            flash(message, 'error')
            return render_template('auth/forgot_password.html')

        allowed, retry_after = consume_action_quota(
            'password_reset_request',
            limit=3,
            window_seconds=900,
            subject=identifier,
        )
        if not allowed:
            message = f'Too many reset requests. Please wait about {retry_after} seconds and try again.'
            if _wants_json_response():
                return _json_response(False, message, status_code=429)
            flash(message, 'error')
            return render_template('auth/forgot_password.html'), 429

        user = _find_user_by_identifier(identifier)
        if user and user.email:
            try:
                OTPService.create_otp(user=user, purpose=OTPService.PURPOSE_PASSWORD_RESET)
                SessionService.record_auth_event('password_reset_requested', user=user, status='info')
                db.session.commit()
                message = 'If the account exists, a password reset code was sent.'
                if _wants_json_response():
                    return _json_response(True, message, status_code=200)
            except ValueError as exc:
                if _wants_json_response():
                    return _json_response(False, str(exc), status_code=429)
                flash(str(exc), 'error')
                return render_template('auth/forgot_password.html')

        message = 'If the account exists, a password reset code was sent.'
        if _wants_json_response():
            return _json_response(True, message, status_code=200)
        flash(message, 'info')
        return redirect(url_for('auth.reset_password'))

    return render_template('auth/forgot_password.html')


@auth_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    """Complete password reset using an emailed OTP."""
    if current_user.is_authenticated:
        return redirect(url_for('missions.index'))

    if request.method == 'POST':
        data = _auth_request_data()
        identifier = (data.get('identifier') or data.get('username') or data.get('email') or '').strip()
        otp_code = (data.get('otp_code') or '').strip()
        password = data.get('password', '')
        confirm_password = data.get('confirm_password', '')
        user = _find_user_by_identifier(identifier)

        if not user or not user.email:
            message = 'Unable to reset password with the supplied details.'
            if _wants_json_response():
                return _json_response(False, message, status_code=404)
            flash(message, 'error')
            return render_template('auth/reset_password.html')

        if password != confirm_password:
            if _wants_json_response():
                return _json_response(False, 'Passwords do not match', status_code=400)
            flash('Passwords do not match', 'error')
            return render_template('auth/reset_password.html')

        try:
            validate_password(password)
        except ValidationError as exc:
            if _wants_json_response():
                return _json_response(False, str(exc), status_code=400)
            flash(str(exc), 'error')
            return render_template('auth/reset_password.html')

        if UserService.is_password_reused(user, password):
            if _wants_json_response():
                return _json_response(False, 'Please choose a password you have not used recently.', status_code=400)
            flash('Please choose a password you have not used recently.', 'error')
            return render_template('auth/reset_password.html')

        allowed, retry_after = consume_action_quota(
            'password_reset_verify',
            limit=6,
            window_seconds=900,
            subject=user.email or user.username,
        )
        if not allowed:
            message = f'Too many reset attempts. Please wait about {retry_after} seconds and try again.'
            if _wants_json_response():
                return _json_response(False, message, status_code=429)
            flash(message, 'error')
            return render_template('auth/reset_password.html'), 429

        verified, message = OTPService.verify_otp(user=user, purpose=OTPService.PURPOSE_PASSWORD_RESET, code=otp_code)
        if not verified:
            register_auth_failure(user.username)
            SessionService.record_auth_event('otp_failure', user=user, status='warning', details='password_reset')
            db.session.commit()
            if _wants_json_response():
                return _json_response(False, message, status_code=400)
            flash(message, 'error')
            return render_template('auth/reset_password.html')

        user.set_password(password)
        user.password_changed_at = utc_now()
        UserService.record_password_history(user)
        SessionService.revoke_all_user_sessions(user)
        clear_auth_failures(user.username)
        SessionService.record_auth_event('password_reset_success', user=user, status='success')
        db.session.commit()
        message = 'Password reset successful. Please sign in.'
        if _wants_json_response():
            return _json_response(True, message, status_code=200)
        flash(message, 'success')
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
