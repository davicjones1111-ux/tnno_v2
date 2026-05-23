"""
Performance Optimization Utilities
Provides caching and optimization functions for the Flask app
"""
from functools import wraps
from flask import request, jsonify
from flask_caching import Cache
import hashlib
import json


def cache_with_user_hash(cache, timeout=300, key_prefix='view'):
    """
    Cache decorator that varies by user authentication status.
    Anonymous users get shared cache, authenticated users get personalized cache.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            from flask_login import current_user

            # Generate cache key based on user status
            user_key = 'anon'
            if current_user.is_authenticated:
                user_key = f'user_{current_user.id}'

            # Create cache key from function name, user, and request args
            cache_key = f"{key_prefix}:{user_key}:{request.endpoint}:{hashlib.md5(json.dumps(request.args, sort_keys=True).encode()).hexdigest()}"

            # Try to get from cache
            rv = cache.get(cache_key)
            if rv is not None:
                return rv

            # Execute function and cache result
            rv = f(*args, **kwargs)
            cache.set(cache_key, rv, timeout=timeout)
            return rv
        return decorated_function
    return decorator


def cached_fragment(cache, timeout=600, key_prefix='fragment'):
    """
    Cache decorator for template fragments or expensive computations.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Create unique key based on function arguments
            args_key = hashlib.md5(json.dumps(args, default=str).encode()).hexdigest()
            kwargs_key = hashlib.md5(json.dumps(kwargs, sort_keys=True, default=str).encode()).hexdigest()
            cache_key = f"{key_prefix}:{f.__name__}:{args_key}:{kwargs_key}"

            rv = cache.get(cache_key)
            if rv is not None:
                return rv

            rv = f(*args, **kwargs)
            cache.set(cache_key, rv, timeout=timeout)
            return rv
        return decorated_function
    return decorator


def invalidate_user_cache(cache, user_id):
    """Invalidate all cache entries for a specific user."""
    from app.extensions import cache

    # Get all cache keys (this is a simplified approach)
    # In production with Redis, you'd use SCAN
    cache_keys = [
        f'view:user_{user_id}:*',
    ]

    # Clear related caches
    cache.clear()


def optimize_query(query, eager_loads=None):
    """
    Optimize a SQLAlchemy query by adding eager loading.
    """
    if eager_loads:
        for rel in eager_loads:
            query = query.options(*eager_loads[rel])
    return query


# Cache timeout constants for different content types
CACHE_TIMEOUTS = {
    'homepage': 300,        # 5 minutes for homepage
    'feed': 60,             # 1 minute for feed (changes frequently)
    'profile': 300,         # 5 minutes for profile
    'leaderboard': 600,     # 10 minutes for leaderboard
    'missions': 120,       # 2 minutes for missions list
    'static': 86400,        # 24 hours for static content
}
