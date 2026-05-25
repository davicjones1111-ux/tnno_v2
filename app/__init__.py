"""
Flask Application Factory
Create and configure the Flask application with all blueprints and extensions
"""
import os
import secrets
import time
from flask import Flask, make_response, jsonify, flash, request, redirect, url_for, g
from sqlalchemy import text, func
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from app.config import config
from app.extensions import init_extensions, verify_database_connection, db, login_manager, cache
from app.datetime_utils import utc_now
from app.services.cloudinary_service import CloudinaryService
from app.game_state import init_game_state
from app.logging_config import configure_logging


def create_app(config_name=None):
    """Application factory function"""

    # Determine config to use
    if config_name is None:
        config_name = (
            os.environ.get('FLASK_ENV')
            or os.environ.get('APP_ENV')
            or ('production' if os.environ.get('RENDER') or os.environ.get('RENDER_EXTERNAL_URL') else 'development')
        )

    # Create Flask app
    app = Flask(__name__,
                template_folder='templates',
                static_folder='static')

    # Load configuration
    app.config.from_object(config.get(config_name, config['development']))
    configure_logging(app)

    if app.config.get('TRUST_PROXY_HEADERS'):
        proxy_hops = int(app.config.get('TRUSTED_PROXY_HOPS', 1))
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=proxy_hops,
            x_proto=proxy_hops,
            x_host=proxy_hops,
            x_port=proxy_hops,
        )

    if config_name == 'production':
        missing_env = [
            name for name in (
                'SECRET_KEY',
                'DATABASE_URL',
            )
            if not os.environ.get(name)
        ]
        if missing_env:
            raise RuntimeError(
                'Missing required production environment variables: ' + ', '.join(missing_env)
            )

    # Initialize extensions
    init_extensions(app)
    app.logger.info('Startup phase complete: extensions initialized')
    verify_database_connection(app)
    app.logger.info('Startup phase complete: database verified')
    CloudinaryService.init_app(app)
    init_game_state(app)
    app.logger.info('Startup phase complete: media and game state initialized')

    if config_name == 'production':
        optional_missing = [
            name for name in (
                'NOWPAYMENTS_API_KEY',
                'NOWPAYMENTS_IPN_SECRET',
            )
            if not os.environ.get(name)
        ]
        if optional_missing:
            app.logger.warning(
                'Optional production env vars missing; deposit creation/webhook features may be limited: %s',
                ', '.join(optional_missing)
            )
        if os.environ.get('RENDER') and not app.extensions.get('cloudinary_enabled'):
            app.logger.warning(
                'Cloudinary is not configured on Render. Uploaded images saved to local disk will disappear after restarts or redeploys.'
            )

    from app.security import _get_client_ip, enforce_rate_limit, enforce_request_guards

    @app.before_request
    def _security_request_guards():
        g.request_started_at = time.perf_counter()
        g.request_id = request.headers.get('X-Request-ID') or secrets.token_hex(8)
        enforce_request_guards()
        enforce_rate_limit()
        from app.services.session_service import SessionService
        SessionService.enforce_current_session()

        if cache.get('security_maintenance_lock') != '1':
            try:
                from app.services.otp_service import OTPService
                SessionService.cleanup_security_records()
                OTPService.cleanup_expired()
            except Exception as exc:
                app.logger.warning('Security maintenance skipped: %s', exc)
            finally:
                cache.set('security_maintenance_lock', '1', timeout=900)

    @app.get('/healthz')
    def healthz():
        try:
            db.session.execute(text('SELECT 1'))
        except Exception as exc:
            app.logger.warning('Health check failed database probe: %s', exc)
            db.session.rollback()
            return jsonify({
                'status': 'degraded',
                'environment': config_name,
                'database': 'unavailable',
            }), 503

        return jsonify({
            'status': 'ok',
            'environment': config_name,
            'database': 'ready',
        }), 200

    # Add aggressive caching headers for maximum speed
    @app.after_request
    def add_cache_headers(response):
        """Add safe cache headers and request access logging."""
        from flask_login import current_user

        content_type = (response.headers.get('Content-Type') or '').lower()
        is_static_asset = request.path.startswith('/static/') or any(
            marker in content_type for marker in ('javascript', 'css', 'image', 'font')
        )

        if is_static_asset:
            response.headers['Cache-Control'] = 'public, max-age=604800, immutable'
        elif 'html' in content_type or 'json' in content_type:
            response.headers['Cache-Control'] = 'no-store, private, must-revalidate'

        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Request-ID'] = getattr(g, 'request_id', '')

        if app.config.get('ENABLE_ACCESS_LOGS', True) and request.endpoint != 'static':
            started = getattr(g, 'request_started_at', None)
            duration_ms = int((time.perf_counter() - started) * 1000) if started else 0
            user_id = current_user.id if current_user.is_authenticated else '-'
            access_logger = app.extensions.get('access_logger')
            if access_logger:
                access_logger.info(
                    'status=%s method=%s path=%r ip=%r user=%r duration_ms=%s referrer=%r ua=%r',
                    response.status_code,
                    request.method,
                    request.full_path or request.path,
                    _get_client_ip() or '-',
                    user_id,
                    duration_ms,
                    request.referrer or '',
                    request.user_agent.string[:250],
                )

        return response

    # Create upload folder with absolute path
    upload_folder = app.config.get('UPLOAD_FOLDER')
    if not os.path.isabs(upload_folder):
        upload_folder = os.path.join(os.path.dirname(__file__), '..', upload_folder)
    os.makedirs(upload_folder, exist_ok=True)

    # Setup login manager user loader
    @login_manager.user_loader
    def load_user(user_id):
        from app.models import User
        try:
            return User.query.get(int(user_id))
        except Exception as exc:
            app.logger.warning(f'User loader failed for id={user_id}: {exc}')
            return None

    # Register blueprints
    register_blueprints(app)
    app.logger.info('Startup phase complete: blueprints registered')

    # Register error handlers
    register_error_handlers(app)
    app.logger.info('Startup phase complete: error handlers registered')

    # Register context processors
    register_context_processors(app)
    app.logger.info('Startup phase complete: context processors registered')

    # Register custom filters
    register_filters(app)
    app.logger.info('Startup phase complete: template filters registered')

    # Create/ensure schema in development-style environments.
    if app.config.get('AUTO_CREATE_SCHEMA_ON_START', True):
        # Import models before create_all so SQLAlchemy knows every table.
        from app import models as _models  # noqa: F401

        # Ensure database directory exists for SQLite
        if 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']:
            db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
            if db_path and db_path != ':memory:':
                db_dir = os.path.dirname(db_path)
                if db_dir:
                    os.makedirs(db_dir, exist_ok=True)
        with app.app_context():
            def safe_schema_step(step_name, func):
                try:
                    func()
                except Exception as exc:
                    db.session.rollback()
                    db.session.remove()
                    print(f"Warning: {step_name} failed during startup: {exc}")

            safe_schema_step('db.create_all', lambda: db.create_all())
            safe_schema_step('DepositService.ensure_deposit_schema', lambda: __import__('app.services.deposit_service', fromlist=['DepositService']).DepositService.ensure_deposit_schema())
            safe_schema_step('MissionService.ensure_mission_schema', lambda: __import__('app.services.mission_service', fromlist=['MissionService']).MissionService.ensure_mission_schema())
            safe_schema_step('SellerService.ensure_seller_schema', lambda: __import__('app.services.seller_service', fromlist=['SellerService']).SellerService.ensure_seller_schema())
            safe_schema_step('NotificationService.ensure_notification_schema', lambda: __import__('app.services.notification_service', fromlist=['NotificationService']).NotificationService.ensure_notification_schema())
            safe_schema_step('MerchService.ensure_merch_schema', lambda: __import__('app.services.merch_service', fromlist=['MerchService']).MerchService.ensure_merch_schema())
            safe_schema_step('HistoryService.ensure_history_schema', lambda: __import__('app.services.history_service', fromlist=['HistoryService']).HistoryService.ensure_history_schema())
            safe_schema_step('ensure_runtime_indexes', ensure_runtime_indexes)
            safe_schema_step('optimize_database', optimize_database)

            # Create admin user if not exists
            from app.models import User
            admin_username = app.config.get('ADMIN_USER', 'admin')
            admin_pass = app.config.get('ADMIN_PASS')
            def create_admin_user():
                if not admin_username or not admin_pass:
                    app.logger.info('Skipping admin bootstrap user creation because ADMIN_USER/ADMIN_PASS is not fully configured')
                    return
                if not User.query.filter_by(username=admin_username).first():
                    admin_user = User(username=admin_username, role='admin')
                    admin_user.set_password(admin_pass)
                    db.session.add(admin_user)
                    db.session.commit()
            safe_schema_step('create_admin_user', create_admin_user)

    # Start background tasks
    register_background_tasks(app)
    app.logger.info('Startup phase complete: background tasks registered')
    app.logger.info('Application startup complete')

    return app


def register_blueprints(app):
    """Register all Flask blueprints"""
    from app.blueprints import get_blueprints

    for domain_blueprints in get_blueprints().values():
        for blueprint, url_prefix in domain_blueprints:
            if url_prefix:
                app.register_blueprint(blueprint, url_prefix=url_prefix)
            else:
                app.register_blueprint(blueprint)


def register_error_handlers(app):
    """Register error handlers"""
    from flask import render_template
    from app.api_utils import error as json_error_helper

    def wants_json_error():
        return (
            request.path.startswith('/api/')
            or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or request.accept_mimetypes['application/json'] >= request.accept_mimetypes['text/html']
        )

    def json_error(message, status):
        return json_error_helper(message, status, request_id=getattr(g, 'request_id', ''))

    @app.errorhandler(404)
    def not_found_error(error):
        if wants_json_error():
            return json_error('Resource not found', 404)
        return render_template('errors/404.html'), 404

    @app.errorhandler(403)
    def forbidden_error(error):
        if wants_json_error():
            return json_error('Access denied', 403)
        return render_template('errors/403.html'), 403

    @app.errorhandler(400)
    def bad_request_error(error):
        if wants_json_error():
            return json_error('Bad request', 400)
        return render_template('errors/400.html'), 400

    @app.errorhandler(413)
    def request_entity_too_large(error):
        if wants_json_error():
            return json_error('Uploaded file is too large', 413)
        flash('Uploaded file is too large.', 'error')
        return redirect(request.referrer or url_for('missions.index'))

    @app.errorhandler(429)
    def rate_limited_error(error):
        if wants_json_error():
            return json_error('Too many requests. Please slow down.', 429)
        return render_template('errors/429.html'), 429

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        app.logger.warning(
            'CSRF validation failed request_id=%s path=%s reason=%s',
            getattr(g, 'request_id', '-'),
            request.path,
            error.description,
        )
        if wants_json_error():
            return json_error('Security check failed. Please refresh and try again.', 400)
        flash('Security check failed. Please refresh and try again.', 'error')
        return redirect(request.referrer or url_for('auth.login'))

    @app.errorhandler(500)
    def internal_error(error):
        app.logger.exception('Unhandled internal server error request_id=%s: %s', getattr(g, 'request_id', '-'), error)
        db.session.rollback()
        if wants_json_error():
            return json_error('Internal server error', 500)
        return render_template('errors/500.html'), 500

    @app.errorhandler(Exception)
    def unhandled_exception(error):
        if isinstance(error, HTTPException):
            return error
        app.logger.exception('Unhandled exception request_id=%s path=%s', getattr(g, 'request_id', '-'), request.path)
        db.session.rollback()
        if wants_json_error():
            return json_error('Internal server error', 500)
        return render_template('errors/500.html'), 500


def register_context_processors(app):
    """Register template context processors"""
    from flask_login import current_user
    from datetime import datetime, timedelta
    import math
    from app.models import SellerNotification, UserNotification
    from app.utils import count_words
    from app.security import get_safe_redirect_target

    @app.before_request
    def before_request():
        """Make current user available in templates"""
        g.current_user = current_user

    @app.before_request
    def seller_expiry_reminder():
        """Auto-remind sellers before plan expiry (once per day)."""
        try:
            if not current_user.is_authenticated:
                return
            if current_user.is_admin():
                return
            if not current_user.is_seller:
                return
            expires_at = current_user.seller_expires_at
            if not expires_at:
                return
            if request.method != 'GET':
                return
            if request.blueprint in {'api'}:
                return
            if request.endpoint in {'static', 'healthz'}:
                return

            now = utc_now()
            if expires_at <= now:
                return

            seconds_left = (expires_at - now).total_seconds()
            days_left = int(math.ceil(seconds_left / 86400))
            if days_left > 7:
                return

            last = current_user.seller_reminder_sent_at
            if last and (now - last) < timedelta(hours=24):
                return

            if days_left == 1:
                message = 'Your seller plan expires in 1 day. Renew to keep products visible.'
            else:
                message = f'Your seller plan expires in {days_left} days. Renew to keep products visible.'

            flash(message, 'warning')
            current_user.seller_reminder_sent_at = now
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            app.logger.warning(f'Seller expiry reminder skipped due to error: {exc}')

    @app.before_request
    def enforce_word_limit():
        """Enforce max word count on submitted text fields."""
        if not app.config.get('WORD_LIMIT_ENABLED', True):
            return
        if request.method not in {'POST', 'PUT', 'PATCH'}:
            return
        if not request.form:
            return

        max_words = int(app.config.get('MAX_WORDS_PER_FIELD', 100))
        for _, value in request.form.items():
            if not isinstance(value, str):
                continue
            if count_words(value) > max_words:
                message = f'Maximum {max_words} words allowed.'
                if request.blueprint == 'api':
                    return jsonify({'error': message}), 400
                flash(message, 'error')
                return redirect(get_safe_redirect_target(request.referrer, 'missions.index'))

    @app.context_processor
    def inject_global_badges():
        """Inject lightweight notification counts for global UI badges - Optimized with caching"""
        try:
            if not current_user.is_authenticated:
                return {}
            
            # Use cache for notification counts to avoid repeated DB queries
            cache_key = f'global_notif_count_{current_user.id}'
            cached_count = cache.get(cache_key)
            if cached_count is not None:
                return {'global_notif_count': cached_count}
            
            # Single combined query for both notification types
            user_notif = db.session.query(func.count(UserNotification.id))\
                .filter(UserNotification.user_id == current_user.id, UserNotification.read_at.is_(None)).scalar() or 0
            
            seller_notif = db.session.query(func.count(SellerNotification.id))\
                .filter(SellerNotification.seller_id == current_user.id, SellerNotification.is_read.is_(False)).scalar() or 0
            
            total_count = user_notif + seller_notif
            
            # Cache for 30 seconds to reduce DB load
            cache.set(cache_key, total_count, timeout=30)
            return {'global_notif_count': total_count}
        except Exception as exc:
            app.logger.warning(f'Global badge injection skipped due to error: {exc}')
            return {'global_notif_count': 0}


def register_filters(app):
    """Register custom Jinja2 filters"""
    import hashlib
    import os
    from flask import url_for

    def _is_remote_media(value):
        return bool(value and str(value).lower().startswith(('http://', 'https://')))

    def _normalize_static_upload_path(value):
        if not value:
            return ''
        v = str(value).lstrip('/')
        if v.startswith('uploads/'):
            return v
        return f"uploads/{v}"

    @app.template_filter('format_number')
    def format_number(value):
        """Format number with thousands separator, supports up to 99,999,999"""
        if value is None:
            return '0'
        try:
            val = int(value)
            # Format with M (million) for values >= 1,000,000
            if val >= 10000000:
                return f'{val/1000000:.1f}M'
            elif val >= 1000000:
                return f'{val/1000000:.0f}M'
            elif val >= 100000:
                return f'{val/1000:.0f}K'
            else:
                return f"{val:,}"
        except (ValueError, TypeError):
            return str(value)

    @app.template_filter('lazy_img')
    def lazy_img(path, alt='', css_class=''):
        """Generate an optimized img tag with lazy loading"""
        if not path:
            return ''

        img_url = path if _is_remote_media(path) else url_for('static', filename=_normalize_static_upload_path(path))
        
        # Generate unique placeholder based on path for consistent loading
        placeholder = f'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300"%3E%3Crect fill="%23f0f0f0" width="400" height="300"/%3E%3C/svg%3E'
        
        class_attr = f'class="{css_class}"' if css_class else ''
        alt_attr = f'alt="{alt}"' if alt else 'alt=""'
        
        # Use data-src for lazy loading, src for placeholder
        return f'<img src="{placeholder}" data-src="{img_url}" {class_attr} {alt_attr} loading="lazy" width="400" height="300">'

    @app.template_filter('static_path')
    def static_path(value):
        """Normalize a stored file path for use with `url_for('static', filename=...)`.
        The database may contain values like 'missions/file.png' or 'uploads/missions/file.png'.
        This filter ensures the returned path always begins with 'uploads/'.
        """
        if not value:
            return ''
        if _is_remote_media(value):
            return str(value)
        return _normalize_static_upload_path(value)

    @app.template_filter('media_url')
    def media_url(value):
        """Return a direct URL for remote media or a static URL for local uploads."""
        if not value:
            return ''
        if _is_remote_media(value):
            return str(value)
        return url_for('static', filename=_normalize_static_upload_path(value))

    @app.template_global('media_url')
    def media_url_global(value):
        return media_url(value)

    @app.template_filter('static_exists')
    def static_exists(value):
        """Return True if the normalized static file exists on disk."""
        if not value:
            return False
        if _is_remote_media(value):
            return True
        path = static_path(value)
        full = os.path.join(app.static_folder, path)
        return os.path.exists(full)

    @app.template_filter('static_version')
    def static_version(filename):
        """Add cache-busting version hash to static file URL."""
        try:
            filepath = os.path.join(app.static_folder, filename)
            if os.path.exists(filepath):
                with open(filepath, 'rb') as f:
                    file_hash = hashlib.md5(f.read()).hexdigest()[:8]
                return f"{filename}?v={file_hash}"
        except Exception:
            pass
        return filename

    @app.template_global('asset_url')
    def asset_url(filename):
        """Build static URL with stable file hash for cache-busting."""
        try:
            filepath = os.path.join(app.static_folder, filename)
            if os.path.exists(filepath):
                with open(filepath, 'rb') as f:
                    file_hash = hashlib.md5(f.read()).hexdigest()[:8]
                return url_for('static', filename=filename, v=file_hash)
        except Exception:
            pass
        return url_for('static', filename=filename)

    @app.template_filter('process_post_content')
    def process_post_content(content):
        """Process post content for 4chan-style formatting:
        - Greentext (>text)
        - Reply links (>>123456)
        """
        if not content:
            return ''

        import re

        # First escape HTML to prevent XSS
        content = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        # Process greentext (>text) - lines starting with >
        content = re.sub(r'^(&gt;.*)$', r'<span class="greentext">\1</span>', content, flags=re.MULTILINE)

        # Process reply links (>>123456) - capture >> and number separately
        content = re.sub(r'(&gt;&gt;)(\d+)', r'<span class="reply-link" onclick="scrollToPost(\2)">\1\2</span>', content)

        # Convert newlines to <br>
        content = content.replace('\n', '<br>')

        return content


def register_background_tasks(app):
    """Run lightweight startup maintenance without changing app behavior."""
    try:
        from app.services.otp_service import OTPService
        from app.services.session_service import SessionService
        OTPService.cleanup_expired()
        SessionService.cleanup_security_records()
    except Exception as exc:
        app.logger.warning('Startup security maintenance skipped: %s', exc)
    app.logger.info('Background maintenance bootstrap complete')


def ensure_runtime_indexes():
    """Create missing runtime indexes for high-traffic pages."""
    from sqlalchemy import text
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_users_coins ON users (coins)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_posts_parent_created ON posts (parent_id, created_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_posts_user_created_at ON posts (user_id, created_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_user_missions_arch_created ON user_missions (is_archived, created_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_work_requests_arch_created ON work_requests (is_archived, created_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_service_orders_arch_created ON service_orders (is_archived, created_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_withdraw_requests_arch_created ON withdraw_requests (is_archived, created_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_withdraw_requests_user_status_created ON withdraw_requests (user_id, status, created_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_deposits_arch_created ON deposits (is_archived, created_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_merch_orders_arch_created ON merch_orders (is_archived, created_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_merch_orders_user_created_at ON merch_orders (user_id, created_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_user_notifications_user_read_created ON user_notifications (user_id, read_at, created_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_seller_notifications_seller_read_created ON seller_notifications (seller_id, is_read, created_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_seller_chat_messages_conversation_created ON seller_chat_messages (conversation_id, created_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_wallet_transactions_user_created ON wallet_transactions (user_id, created_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_user_sessions_user_activity ON user_sessions (user_id, last_activity_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_user_sessions_user_revoked ON user_sessions (user_id, revoked_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_auth_events_user_created ON auth_events (user_id, created_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_auth_events_type_created ON auth_events (event_type, created_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_email_otps_email_purpose_created ON email_otps (email, purpose, created_at)'))
    db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_admin_audit_action_created ON admin_audit_logs (action, created_at)'))
    db.session.commit()


def optimize_database():
    """Optimize database for better performance - enable WAL mode and pragmas."""
    from sqlalchemy import text

    engine_url = str(db.engine.url).lower()
    if 'sqlite' not in engine_url:
        return

    # Enable WAL mode for better concurrent read/write performance
    try:
        db.session.execute(text('PRAGMA journal_mode=WAL'))
        db.session.execute(text('PRAGMA synchronous=NORMAL'))
        db.session.execute(text('PRAGMA cache_size=-64000'))  # 64MB cache
        db.session.execute(text('PRAGMA temp_store=MEMORY'))
        db.session.commit()
    except Exception:
        # WAL mode may not be available on all SQLite versions
        pass
