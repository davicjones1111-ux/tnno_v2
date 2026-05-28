"""
RetroQuest Platform Configuration
Production-ready Flask configuration with environment variable support
Optimized for 100K+ users
"""
import os
from datetime import timedelta
from urllib.parse import urlparse


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def _env(name: str, default=None):
    value = os.environ.get(name)
    if value is None:
        return default
    return value


def _normalize_database_url(value):
    """Normalize provider DB URLs for SQLAlchemy compatibility."""
    if not value:
        return value
    if value.startswith('postgres://'):
        return 'postgresql://' + value[len('postgres://'):]
    return value


def _normalize_cache_type(value):
    """Map legacy Flask-Caching aliases to backend class paths."""
    raw = (value or 'simple').strip()
    lowered = raw.lower()
    if lowered == 'simple':
        return 'flask_caching.backends.simplecache.SimpleCache'
    if lowered == 'redis':
        return 'flask_caching.backends.rediscache.RedisCache'
    return raw


def _default_csp():
    return {
        'default-src': ["'self'"],
        'base-uri': ["'self'"],
        'form-action': ["'self'"],
        'frame-ancestors': ["'self'"],
        'frame-src': ["'self'"],
        'img-src': ["'self'", 'data:', 'blob:', 'https:'],
        'font-src': ["'self'", 'data:', 'https://fonts.gstatic.com'],
        'media-src': ["'self'", 'blob:', 'https:'],
        'connect-src': ["'self'", 'https:', 'wss:', 'ws:'],
        'script-src': ["'self'", "'unsafe-inline'", 'https://cdn.jsdelivr.net'],
        'style-src': ["'self'", "'unsafe-inline'", 'https://fonts.googleapis.com'],
        'object-src': ["'none'"],
    }


def _build_engine_options(database_uri: str):
    if 'sqlite' in database_uri:
        return {
            'pool_pre_ping': True,
        }

    connect_timeout = int(os.environ.get('DB_CONNECT_TIMEOUT') or '10')
    statement_timeout_ms = int(os.environ.get('DB_STATEMENT_TIMEOUT_MS') or '30000')
    idle_tx_timeout_ms = int(os.environ.get('DB_IDLE_IN_TRANSACTION_TIMEOUT_MS') or '30000')
    options = {
        'pool_pre_ping': True,
        'pool_recycle': int(os.environ.get('DB_POOL_RECYCLE') or '300'),
        'pool_size': int(os.environ.get('DB_POOL_SIZE') or '10'),
        'max_overflow': int(os.environ.get('DB_MAX_OVERFLOW') or '20'),
        'pool_timeout': int(os.environ.get('DB_POOL_TIMEOUT') or '30'),
        'pool_use_lifo': True,
    }

    parsed = urlparse(database_uri)
    if parsed.scheme.startswith('postgresql'):
        hostname = (parsed.hostname or '').lower()
        is_neon_pooler = 'pooler' in hostname and ('neon.tech' in hostname or 'neon.com' in hostname)
        include_startup_options = _bool_env('DB_INCLUDE_STARTUP_OPTIONS', not is_neon_pooler)

        ssl_mode = os.environ.get('DB_SSL_MODE') or ('require' if os.environ.get('RENDER') else 'prefer')
        connect_args = {
            'sslmode': ssl_mode,
            'connect_timeout': connect_timeout,
        }
        if include_startup_options:
            connect_args['options'] = (
                f'-c statement_timeout={statement_timeout_ms} '
                f'-c idle_in_transaction_session_timeout={idle_tx_timeout_ms}'
            )
        options['connect_args'] = connect_args
    return options


class Config:
    """Base configuration class"""

    # Secret Key
    SECRET_KEY = _env('SECRET_KEY') or os.urandom(32).hex()
    AUTO_CREATE_SCHEMA_ON_START = _bool_env('AUTO_CREATE_SCHEMA_ON_START', True)

    # Database Configuration
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, '..'))
    SQLALCHEMY_DATABASE_URI = _normalize_database_url(_env('DATABASE_URL')) or \
        f'sqlite:///{os.path.join(BASE_DIR, "..", "instance", "database.db")}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SQLALCHEMY_ENGINE_OPTIONS = _build_engine_options(SQLALCHEMY_DATABASE_URI)

    # Cache Configuration - Optimized for high traffic
    # For production with 100K+ users, use Redis: CACHE_TYPE = 'redis'
    CACHE_TYPE = _normalize_cache_type(os.environ.get('CACHE_TYPE'))
    REDIS_URL = os.environ.get('REDIS_URL') or 'redis://localhost:6379/0'
    CACHE_REDIS_URL = os.environ.get('CACHE_REDIS_URL') or REDIS_URL
    CACHE_DEFAULT_TIMEOUT = int(os.environ.get('CACHE_TIMEOUT') or '60')  # 1 minute default
    CACHE_KEY_PREFIX = 'retroquest:'
    
    # Aggressive caching settings for maximum speed
    CACHE_THRESHOLD = 500  # Cache at least 500 items
    CACHE_NULL_NONE = True  # Cache None results
    
    # Static file caching (for production) - aggressive
    SEND_FILE_MAX_AGE_DEFAULT = 86400  # 24 hours for static files
    SESSION_COOKIE_SECURE = False

    # Compression Configuration - Maximum compression for fastest transfer
    COMPRESS_MIMETYPES = ['text/html', 'text/css', 'text/javascript',
                         'application/javascript', 'application/json',
                         'image/svg+xml', 'application/xml', 'text/plain',
                         'application/vnd.ms-fontobject', 'application/x-font-ttf']
    COMPRESS_LEVEL = 9  # Maximum compression
    COMPRESS_MIN_SIZE = 200  # Compress even small files

    # Upload Configuration
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER') or os.path.join(BASE_DIR, 'static', 'uploads')
    MAX_CONTENT_LENGTH = int(os.environ.get('MAX_CONTENT_LENGTH') or str(10 * 1024 * 1024))
    MAX_WORDS_PER_FIELD = int(os.environ.get('MAX_WORDS_PER_FIELD') or '100')
    WORD_LIMIT_ENABLED = _bool_env('WORD_LIMIT_ENABLED', True)
    NOTIFICATION_ALLOWED_EXTENSIONS = {
        'png', 'jpg', 'jpeg', 'gif', 'webp',
        'pdf', 'zip', 'rar', 'txt', 'doc', 'docx'
    }
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'}
    CLOUDINARY_CLOUD_NAME = _env('CLOUDINARY_CLOUD_NAME')
    CLOUDINARY_API_KEY = _env('CLOUDINARY_API_KEY')
    CLOUDINARY_API_SECRET = _env('CLOUDINARY_API_SECRET')
    CLOUDINARY_UPLOAD_FOLDER = _env('CLOUDINARY_UPLOAD_FOLDER') or 'retroquest'

    # Session Configuration
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    SESSION_COOKIE_NAME = os.environ.get('SESSION_COOKIE_NAME') or 'retroquest_session'
    SESSION_REFRESH_EACH_REQUEST = True
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_NAME = os.environ.get('REMEMBER_COOKIE_NAME') or 'retroquest_remember'
    REMEMBER_COOKIE_REFRESH_EACH_REQUEST = False
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_SECURE = False
    PREFERRED_URL_SCHEME = os.environ.get('PREFERRED_URL_SCHEME') or 'http'
    TRUST_PROXY_HEADERS = _bool_env('TRUST_PROXY_HEADERS', False)
    TRUSTED_PROXY_HOPS = int(os.environ.get('TRUSTED_PROXY_HOPS') or '1')
    LOGIN_SESSION_PROTECTION = os.environ.get('LOGIN_SESSION_PROTECTION') or 'basic'
    BCRYPT_LOG_ROUNDS = int(os.environ.get('BCRYPT_LOG_ROUNDS') or '13')
    SESSION_INACTIVITY_TIMEOUT_MINUTES = int(os.environ.get('SESSION_INACTIVITY_TIMEOUT_MINUTES') or '720')
    SESSION_ABSOLUTE_TIMEOUT_DAYS = int(os.environ.get('SESSION_ABSOLUTE_TIMEOUT_DAYS') or '30')
    SESSION_ACTIVITY_GRACE_SECONDS = int(os.environ.get('SESSION_ACTIVITY_GRACE_SECONDS') or '60')

    # CSRF Protection (enabled by default)
    CSRF_ENABLED = _bool_env('CSRF_ENABLED', True)
    WTF_CSRF_ENABLED = _bool_env('WTF_CSRF_ENABLED', True)
    WTF_CSRF_TIME_LIMIT = int(os.environ.get('WTF_CSRF_TIME_LIMIT') or '3600')
    WTF_CSRF_HEADERS = ['X-CSRFToken', 'X-CSRF-Token']

    # Rate Limiting (per-IP + per-user)
    RATE_LIMIT_ENABLED = _bool_env('RATE_LIMIT_ENABLED', True)
    RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get('RATE_LIMIT_WINDOW_SECONDS') or '60')
    RATE_LIMIT_PER_IP = int(os.environ.get('RATE_LIMIT_PER_IP') or '600')
    RATE_LIMIT_PER_USER = int(os.environ.get('RATE_LIMIT_PER_USER') or '300')
    RATE_LIMIT_TRUST_PROXY_HEADERS = _bool_env('RATE_LIMIT_TRUST_PROXY_HEADERS', False)
    RATE_LIMIT_EXEMPT_ENDPOINTS = ('static', 'healthz')
    BRUTE_FORCE_WINDOW_SECONDS = int(os.environ.get('BRUTE_FORCE_WINDOW_SECONDS') or '900')
    BRUTE_FORCE_MAX_ATTEMPTS = int(os.environ.get('BRUTE_FORCE_MAX_ATTEMPTS') or '8')
    BRUTE_FORCE_LOCKOUT_SECONDS = int(os.environ.get('BRUTE_FORCE_LOCKOUT_SECONDS') or '1800')
    SUSPICIOUS_REQUEST_WINDOW_SECONDS = int(os.environ.get('SUSPICIOUS_REQUEST_WINDOW_SECONDS') or '600')
    SUSPICIOUS_REQUEST_LIMIT = int(os.environ.get('SUSPICIOUS_REQUEST_LIMIT') or '6')
    SUSPICIOUS_REQUEST_LOCKOUT_SECONDS = int(os.environ.get('SUSPICIOUS_REQUEST_LOCKOUT_SECONDS') or '1800')
    OTP_EXPIRATION_MINUTES = int(os.environ.get('OTP_EXPIRATION_MINUTES') or '10')
    OTP_RESEND_COOLDOWN_SECONDS = int(os.environ.get('OTP_RESEND_COOLDOWN_SECONDS') or '60')
    OTP_MAX_ATTEMPTS = int(os.environ.get('OTP_MAX_ATTEMPTS') or '5')
    OTP_RETENTION_HOURS = int(os.environ.get('OTP_RETENTION_HOURS') or '24')
    AUTH_EVENT_RETENTION_DAYS = int(os.environ.get('AUTH_EVENT_RETENTION_DAYS') or '60')
    SESSION_RETENTION_DAYS = int(os.environ.get('SESSION_RETENTION_DAYS') or '45')

    # Optional server-side session storage (recommended for large clusters)
    ENABLE_SERVER_SIDE_SESSIONS = _bool_env('ENABLE_SERVER_SIDE_SESSIONS', False)
    SESSION_TYPE = os.environ.get('SESSION_TYPE') or 'redis'
    SESSION_REDIS_URL = os.environ.get('SESSION_REDIS_URL') or REDIS_URL
    SESSION_PERMANENT = True
    SESSION_USE_SIGNER = True
    SESSION_KEY_PREFIX = os.environ.get('SESSION_KEY_PREFIX') or 'retroquest:session:'

    # NowPayments payment gateway configuration
    NOWPAYMENTS_API_KEY = _env('NOWPAYMENTS_API_KEY')
    NOWPAYMENTS_API_URL = _env('NOWPAYMENTS_API_URL') or 'https://api.nowpayments.io/v1/invoice'
    NOWPAYMENTS_IPN_SECRET = _env('NOWPAYMENTS_IPN_SECRET')
    NOWPAYMENTS_CALLBACK_URL = _env('NOWPAYMENTS_CALLBACK_URL')
    NOWPAYMENTS_SUCCESS_URL = _env('NOWPAYMENTS_SUCCESS_URL')
    NOWPAYMENTS_CANCEL_URL = _env('NOWPAYMENTS_CANCEL_URL')
    NOWPAYMENTS_ALLOWED_HOSTS = tuple(
        host.strip().lower()
        for host in (os.environ.get('NOWPAYMENTS_ALLOWED_HOSTS') or 'nowpayments.io').split(',')
        if host.strip()
    )
    
    # Deposit conversion settings used by the NowPayments flow.
    USDT_TO_POINTS = int(os.environ.get('USDT_TO_POINTS') or '4000')
    MIN_DEPOSIT_USDT = float(os.environ.get('MIN_DEPOSIT_USDT') or '5')
    
    # Deposit Configuration
    DEPOSIT_TIMEOUT = int(os.environ.get('DEPOSIT_TIMEOUT') or '1200')  # 20 minutes
    # Admin Configuration
    ADMIN_USER = os.environ.get('ADMIN_USER') or 'admin'
    ADMIN_PASS = os.environ.get('ADMIN_PASS') or ''
    RESEND_API_KEY = _env('RESEND_API_KEY')
    RESEND_FROM_EMAIL = 'onboarding@resend.dev'
    RESEND_REPLY_TO = _env('RESEND_REPLY_TO')
    RESEND_API_BASE_URL = _env('RESEND_API_BASE_URL') or 'https://api.resend.com/emails'

    # Game Configuration
    GAME_PORT = int(os.environ.get('GAME_PORT') or '3000')
    GAME_STATE_BACKEND = os.environ.get('GAME_STATE_BACKEND') or 'memory'
    GAME_STATE_PREFIX = os.environ.get('GAME_STATE_PREFIX') or 'retroquest:game'
    GAME_STATE_LOCK_TIMEOUT = int(os.environ.get('GAME_STATE_LOCK_TIMEOUT') or '10')
    GAME_STATE_LOCK_BLOCKING_TIMEOUT = int(os.environ.get('GAME_STATE_LOCK_BLOCKING_TIMEOUT') or '5')
    GAME_ROOM_TTL_SECONDS = int(os.environ.get('GAME_ROOM_TTL_SECONDS') or '7200')

    # Migration
    MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'migrations')

    # Performance Settings
    # Maximum number of posts to load per page (optimized for large datasets)
    POSTS_PER_PAGE = int(os.environ.get('POSTS_PER_PAGE') or '20')
    MESSAGES_PER_PAGE = int(os.environ.get('MESSAGES_PER_PAGE') or '20')

    # Work Requests
    WORK_REQUEST_FEE_TNNO = int(os.environ.get('WORK_REQUEST_FEE_TNNO') or '10000')

    # Response timeout
    RESPONSE_TIMEOUT = 30
    SLOW_REQUEST_THRESHOLD_MS = int(os.environ.get('SLOW_REQUEST_THRESHOLD_MS') or '250')
    ENABLE_ACCESS_LOGS = _bool_env('ENABLE_ACCESS_LOGS', True)

    # Enable query caching
    SQLALCHEMY_RECORD_QUERIES = False

    # JSON settings for faster encoding
    JSON_SORT_KEYS = False
    JSONIFY_PRETTYPRINT_REGULAR = False
    PASSWORD_MIN_LENGTH = int(os.environ.get('PASSWORD_MIN_LENGTH') or '8')
    PASSWORD_HISTORY_COUNT = int(os.environ.get('PASSWORD_HISTORY_COUNT') or '5')
    CLIENT_ERROR_LOGGING_ENABLED = _bool_env('CLIENT_ERROR_LOGGING_ENABLED', True)

    # Security headers
    CONTENT_SECURITY_POLICY = _default_csp()
    TALISMAN_FORCE_HTTPS = _bool_env('TALISMAN_FORCE_HTTPS', False)
    TALISMAN_STRICT_TRANSPORT_SECURITY = _bool_env('TALISMAN_STRICT_TRANSPORT_SECURITY', False)
    TALISMAN_STRICT_TRANSPORT_SECURITY_MAX_AGE = int(
        os.environ.get('TALISMAN_STRICT_TRANSPORT_SECURITY_MAX_AGE') or '31536000'
    )
    TALISMAN_REFERRER_POLICY = os.environ.get('TALISMAN_REFERRER_POLICY') or 'strict-origin-when-cross-origin'
    TALISMAN_CONTENT_SECURITY_POLICY_REPORT_ONLY = _bool_env(
        'TALISMAN_CONTENT_SECURITY_POLICY_REPORT_ONLY', False
    )

    # Logging
    LOG_DIR = os.environ.get('LOG_DIR') or 'logs'
    LOG_LEVEL = os.environ.get('LOG_LEVEL') or 'INFO'
    LOG_MAX_BYTES = int(os.environ.get('LOG_MAX_BYTES') or str(10 * 1024 * 1024))
    LOG_BACKUP_COUNT = int(os.environ.get('LOG_BACKUP_COUNT') or '10')
    LOG_TO_STDOUT = _bool_env('LOG_TO_STDOUT', True)

class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    TESTING = False
    LOGIN_SESSION_PROTECTION = os.environ.get('LOGIN_SESSION_PROTECTION') or 'basic'


class ProductionConfig(Config):
    """Production configuration - Optimized for 100K+ users"""
    DEBUG = False
    TESTING = False
    AUTO_CREATE_SCHEMA_ON_START = _bool_env('AUTO_CREATE_SCHEMA_ON_START', True)
    _HAS_REDIS_URL = bool(_env('REDIS_URL') or _env('CACHE_REDIS_URL') or _env('SESSION_REDIS_URL'))

    # In production, use DATABASE_URL from the environment. Do not silently fall back to SQLite.
    SQLALCHEMY_DATABASE_URI = _normalize_database_url(_env('DATABASE_URL')) or ''

    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = _env('PREFERRED_URL_SCHEME') or 'https'
    TRUST_PROXY_HEADERS = _bool_env('TRUST_PROXY_HEADERS', True)
    LOGIN_SESSION_PROTECTION = os.environ.get('LOGIN_SESSION_PROTECTION') or 'strong'
    TALISMAN_FORCE_HTTPS = _bool_env('TALISMAN_FORCE_HTTPS', True)
    TALISMAN_STRICT_TRANSPORT_SECURITY = _bool_env('TALISMAN_STRICT_TRANSPORT_SECURITY', True)

    # Use Redis only when a Redis service is actually configured.
    CACHE_TYPE = _normalize_cache_type(_env('CACHE_TYPE') or ('redis' if _HAS_REDIS_URL else 'simple'))
    ENABLE_SERVER_SIDE_SESSIONS = _bool_env('ENABLE_SERVER_SIDE_SESSIONS', _HAS_REDIS_URL)
    GAME_STATE_BACKEND = _env('GAME_STATE_BACKEND') or ('redis' if _HAS_REDIS_URL else 'memory')

    NOWPAYMENTS_API_KEY = _env('NOWPAYMENTS_API_KEY')
    NOWPAYMENTS_IPN_SECRET = _env('NOWPAYMENTS_IPN_SECRET')
    NOWPAYMENTS_CALLBACK_URL = _env('NOWPAYMENTS_CALLBACK_URL')
    NOWPAYMENTS_SUCCESS_URL = _env('NOWPAYMENTS_SUCCESS_URL')
    NOWPAYMENTS_CANCEL_URL = _env('NOWPAYMENTS_CANCEL_URL')

    # Database connection pool for production
    _PROD_DB_URL = _normalize_database_url(_env('DATABASE_URL', ''))
    if 'sqlite' in _PROD_DB_URL or not _PROD_DB_URL:
        SQLALCHEMY_ENGINE_OPTIONS = {
            'pool_pre_ping': True,
        }
    else:
        SQLALCHEMY_ENGINE_OPTIONS = _build_engine_options(_PROD_DB_URL)

    # Longer cache times for production
    CACHE_DEFAULT_TIMEOUT = int(os.environ.get('CACHE_TIMEOUT') or '300')  # 5 minutes


class TestingConfig(Config):
    """Testing configuration"""
    TESTING = True
    SQLALCHEMY_DATABASE_URI = _normalize_database_url(_env('DATABASE_URL')) or 'sqlite:///:memory:'
    CSRF_ENABLED = False
    WTF_CSRF_ENABLED = False
    AUTO_CREATE_SCHEMA_ON_START = True
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    ENABLE_SERVER_SIDE_SESSIONS = False
    CACHE_TYPE = _normalize_cache_type('simple')


# Configuration dictionary
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
