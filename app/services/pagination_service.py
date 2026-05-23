"""
Pagination/query helpers for consistent API limits.
"""
from __future__ import annotations

from app.validators import parse_pagination


class PaginationService:
    DEFAULT_PAGE_SIZE = 20

    @staticmethod
    def get_page_args(page: int | None, per_page: int | None = None):
        return parse_pagination(page=page, per_page=per_page, max_per_page=PaginationService.DEFAULT_PAGE_SIZE)

    @staticmethod
    def paginate(query, page: int | None, per_page: int | None = None):
        args = PaginationService.get_page_args(page=page, per_page=per_page)
        return query.paginate(page=args.page, per_page=args.per_page, error_out=False)
