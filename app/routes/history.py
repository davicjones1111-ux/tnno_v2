"""
History Routes
Unified active/archived history pages for users and admins.
"""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.services.history_service import HistoryService


history_bp = Blueprint('history', __name__)


def _is_partial_request() -> bool:
    return (
        request.args.get('partial') == '1'
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    )


def _normalize_status_for_page(default_status: str) -> str:
    status = (request.args.get('status') or default_status).strip().lower()
    return status if status in HistoryService.STATUS_MAP else default_status


@history_bp.route('/history')
@login_required
def index():
    """Recent user history (latest 10, filterable by type)."""
    selected_type = HistoryService.normalize_user_filter(request.args.get('type'))
    items = HistoryService.get_user_recent_history(
        user_id=current_user.id,
        filter_key=selected_type,
        limit=HistoryService.ACTIVE_LIMIT,
    )

    if _is_partial_request():
        return render_template(
            'history/_table.html',
            items=items,
            empty_title='No recent history',
            empty_body='Your recent activity will appear here.',
        )

    return render_template(
        'history/index.html',
        items=items,
        filters=HistoryService.USER_FILTERS,
        active_type=selected_type,
    )


@history_bp.route('/history/old')
@login_required
def old():
    """Archived user history."""
    selected_type = HistoryService.normalize_user_filter(request.args.get('type'))
    page = request.args.get('page', 1, type=int)
    page_data = HistoryService.get_user_old_history(
        user_id=current_user.id,
        filter_key=selected_type,
        page=page,
        per_page=20,
    )

    return render_template(
        'history/old.html',
        page_data=page_data,
        filters=HistoryService.USER_FILTERS,
        active_type=selected_type,
    )


@history_bp.route('/admin/history')
@login_required
def admin_index():
    """Admin history queue with type + status filters."""
    if not current_user.is_admin():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))

    selected_type = HistoryService.normalize_admin_filter(request.args.get('type'))
    selected_status = _normalize_status_for_page(default_status='pending')
    page = request.args.get('page', 1, type=int)

    page_data = HistoryService.get_admin_history(
        filter_key=selected_type,
        status_key=selected_status,
        page=page,
        per_page=30,
        include_old=False,
    )

    if _is_partial_request():
        return render_template(
            'admin/_history_table.html',
            page_data=page_data,
            endpoint='history.admin_index',
            type_key=selected_type,
            status_key=selected_status,
        )

    return render_template(
        'admin/history.html',
        page_data=page_data,
        type_filters=HistoryService.ADMIN_FILTERS,
        status_filters=HistoryService.STATUS_FILTERS,
        active_type=selected_type,
        active_status=selected_status,
    )


@history_bp.route('/admin/history/old')
@login_required
def admin_old():
    """Admin old/completed history list."""
    if not current_user.is_admin():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))

    selected_type = HistoryService.normalize_admin_filter(request.args.get('type'))
    selected_status = _normalize_status_for_page(default_status='all')
    page = request.args.get('page', 1, type=int)

    page_data = HistoryService.get_admin_history(
        filter_key=selected_type,
        status_key=selected_status,
        page=page,
        per_page=30,
        include_old=True,
    )

    return render_template(
        'admin/history_old.html',
        page_data=page_data,
        type_filters=HistoryService.ADMIN_FILTERS,
        status_filters=HistoryService.STATUS_FILTERS,
        active_type=selected_type,
        active_status=selected_status,
    )
