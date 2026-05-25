"""
Shared JSON response helpers for API endpoints.
"""
from __future__ import annotations

from flask import g, jsonify

from app.services.pagination_service import PaginationService


def ok(payload=None, status: int = 200):
    data = {'success': True, 'request_id': getattr(g, 'request_id', '')}
    if payload:
        data.update(payload)
    return jsonify(data), status


def error(message: str, status: int = 400, **extra):
    data = {'success': False, 'error': message, 'request_id': getattr(g, 'request_id', '')}
    if extra:
        data.update(extra)
    return jsonify(data), status


def paginate_request_args(request, default_per_page: int = 20):
    return PaginationService.get_page_args(
        page=request.args.get('page', 1, type=int),
        per_page=request.args.get('per_page', default_per_page, type=int),
    )


def paginated(items_page, *, key: str = 'items', serializer=None):
    serialize = serializer or (lambda item: item)
    return jsonify({
        key: [serialize(item) for item in items_page.items],
        'page': items_page.page,
        'pages': items_page.pages,
        'per_page': items_page.per_page,
        'total': items_page.total,
        'has_next': items_page.has_next,
        'has_prev': items_page.has_prev,
    })
