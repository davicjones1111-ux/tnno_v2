"""
Flask Extensions
Initialize all Flask extensions for the application
"""
from datetime import timedelta
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf import CSRFProtect
from flask_bcrypt import Bcrypt
from flask_caching import Cache
try:
    from flask_compress import Compress
except Exception:  # pragma: no cover - optional dependency during local dev
    Compress = None
try:
    import redis
except Exception:  # pragma: no cover - optional in local setups
    redis = None
try:
    from flask_session import Session
except Exception:  # pragma: no cover - optional dependency during local dev
    Session = None
try:
    from flask_talisman import Talisman
except Exception:  # pragma: no cover - optional dependency during local dev
    Talisman = None

# Initialize extensions
db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()
bcrypt = Bcrypt()
cache = Cache()
compress = Compress() if Compress else None
session_ext = Session() if Session else None
talisman = Talisman() if Talisman else None


def _is_redis_cache_backend(cache_type):
    value = (cache_type or '').lower()
    return value == 'redis' or 'rediscache' in value


def init_extensions(app):
    """Initialize all Flask extensions with the app"""
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    bcrypt.init_app(app)
    try:
        cache.init_app(app)
    except Exception as exc:
        # Local fallback: keep app booting even if redis backend isn't available.
        app.logger.warning(f'Cache backend init failed, falling back to simple cache: {exc}')
        app.config['CACHE_TYPE'] = 'flask_caching.backends.simplecache.SimpleCache'
        cache.init_app(app)
    if compress is None:
        app.logger.warning('Flask-Compress is unavailable; continuing without response compression')
    else:
        compress.init_app(app)

    # Shared Redis client (cache/session/game-state helpers can reuse this) only when needed.
    uses_redis = (
        _is_redis_cache_backend(app.config.get('CACHE_TYPE'))
        or app.config.get('GAME_STATE_BACKEND') == 'redis'
        or (
            app.config.get('ENABLE_SERVER_SIDE_SESSIONS')
            and app.config.get('SESSION_TYPE') == 'redis'
        )
    )
    redis_url = app.config.get('REDIS_URL') or app.config.get('CACHE_REDIS_URL')
    if uses_redis and redis_url and redis is not None:
        try:
            client = redis.Redis.from_url(redis_url, decode_responses=True)
            client.ping()
            app.extensions['redis_client'] = client
        except Exception as exc:
            app.logger.warning(f'Redis unavailable at {redis_url}; disabling redis_client fallback: {exc}')
            app.extensions['redis_client'] = None
    else:
        app.extensions['redis_client'] = None
    
    # Enable CSRF protection (always on unless explicitly disabled)
    if app.config.get('CSRF_ENABLED', True):
        csrf.init_app(app)

    if talisman is None:
        app.logger.warning('Flask-Talisman is unavailable; continuing without managed security headers')
    else:
        talisman.init_app(
            app,
            force_https=app.config.get('TALISMAN_FORCE_HTTPS', False),
            frame_options='SAMEORIGIN',
            strict_transport_security=app.config.get('TALISMAN_STRICT_TRANSPORT_SECURITY', False),
            strict_transport_security_max_age=app.config.get(
                'TALISMAN_STRICT_TRANSPORT_SECURITY_MAX_AGE', 31536000
            ),
            strict_transport_security_include_subdomains=True,
            strict_transport_security_preload=True,
            content_security_policy=app.config.get('CONTENT_SECURITY_POLICY'),
            content_security_policy_report_only=app.config.get(
                'TALISMAN_CONTENT_SECURITY_POLICY_REPORT_ONLY', False
            ),
            referrer_policy=app.config.get('TALISMAN_REFERRER_POLICY'),
            x_content_type_options=True,
            x_xss_protection=True,
        )

    # Configure login manager - PRO APP STYLE
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to continue'
    login_manager.session_protection = app.config.get('LOGIN_SESSION_PROTECTION', 'basic')
    
    # Session configuration for persistent login (like pro apps)
    # Use defaults only; do not override environment-specific config values.
    app.config.setdefault('SESSION_COOKIE_HTTPONLY', True)  # Prevent XSS attacks
    app.config.setdefault('SESSION_COOKIE_SAMESITE', 'Lax')  # CSRF protection while allowing navigation
    app.config.setdefault('PERMANENT_SESSION_LIFETIME', timedelta(days=30))
    app.config.setdefault('SESSION_REFRESH_EACH_REQUEST', True)
    app.config.setdefault('REMEMBER_COOKIE_DURATION', timedelta(days=14))
    app.config.setdefault('REMEMBER_COOKIE_REFRESH_EACH_REQUEST', False)

    if app.config.get('ENABLE_SERVER_SIDE_SESSIONS'):
        if session_ext is None:
            app.logger.warning('ENABLE_SERVER_SIDE_SESSIONS is set but Flask-Session is not installed')
        else:
            if app.config.get('SESSION_TYPE') == 'redis' and redis is None:
                app.logger.warning('Server-side sessions require redis package; disabling server-side sessions')
                return
            session_redis = app.config.get('SESSION_REDIS_URL')
            if session_redis and redis is not None:
                app.config['SESSION_REDIS'] = redis.Redis.from_url(session_redis, decode_responses=True)
            session_ext.init_app(app)


def verify_database_connection(app):
    """Verify runtime DB connectivity and log the active backend."""
    from sqlalchemy import text

    with app.app_context():
        row = db.session.execute(text('SELECT 1')).scalar()
        if row != 1:
            raise RuntimeError('Database connectivity check failed')
        app.logger.info('Database ready: %s', app.config.get('SQLALCHEMY_DATABASE_URI'))
