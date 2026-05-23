"""
History Service
Unified history sync + filtered query helpers for user and admin views.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import inspect, or_, text

from app.extensions import db
from app.datetime_utils import utc_now
from app.models import (
    Deposit,
    HistoryEntry,
    MerchOrder,
    SellerReport,
    SellerRequest,
    UserNotification,
    Product,
    ServiceOrder,
    UserMission,
    WithdrawRequest,
    WorkRequest,
)


class HistoryService:
    """History aggregation + archive/filter policy."""

    RETENTION_DAYS = 30
    ACTIVE_LIMIT = 10
    SYNC_COOLDOWN_SECONDS = 20

    SOURCE_CONFIG = (
        {'key': 'missions', 'model': UserMission, 'created_field': 'created_at'},
        {'key': 'work_requests', 'model': WorkRequest, 'created_field': 'created_at'},
        {'key': 'service_orders', 'model': ServiceOrder, 'created_field': 'created_at'},
        {'key': 'purchases', 'model': MerchOrder, 'created_field': 'created_at'},
        {'key': 'deposits', 'model': Deposit, 'created_field': 'created_at'},
        {'key': 'withdrawals', 'model': WithdrawRequest, 'created_field': 'created_at'},
    )

    TERMINAL_STATUSES = {
        'missions': {'completed', 'rejected'},
        'work_requests': {'accepted', 'rejected'},
        'service_orders': {'completed', 'rejected'},
        'purchases': {'completed', 'delivered', 'refunded'},
        'deposits': {'success', 'expired'},
        'withdrawals': {'approved', 'rejected'},
    }

    USER_FILTERS = (
        {'key': 'all', 'label': 'All'},
        {'key': 'submissions', 'label': 'Submissions'},
        {'key': 'work_requests', 'label': 'Work Requests'},
        {'key': 'service_requests', 'label': 'Service Requests'},
        {'key': 'deposits', 'label': 'Deposits'},
        {'key': 'withdrawals', 'label': 'Withdrawals'},
        {'key': 'purchases', 'label': 'Purchases (Buy)'},
        {'key': 'sales', 'label': 'Sales (Sell)'},
    )

    USER_FILTER_TYPES = {
        'all': None,
        'submissions': ('submission',),
        'work_requests': ('work_request',),
        'service_requests': ('order',),
        'deposits': ('deposit',),
        'withdrawals': ('withdrawal',),
        'purchases': ('purchase',),
        'sales': ('sale',),
    }

    ADMIN_FILTERS = (
        {'key': 'all', 'label': 'All'},
        {'key': 'submissions', 'label': 'Submissions'},
        {'key': 'work_requests', 'label': 'Work Requests'},
        {'key': 'deposits', 'label': 'Deposits'},
        {'key': 'orders', 'label': 'Orders'},
        {'key': 'users_activity', 'label': 'Users Activity'},
    )

    ADMIN_FILTER_TYPES = {
        'all': None,
        'submissions': ('submission',),
        'work_requests': ('work_request',),
        'deposits': ('deposit',),
        'withdrawals': ('withdrawal',),
        'missions': ('mission',),
        'service_orders': ('order',),
        'merch_products': ('merch_product',),
        'seller_requests': ('seller_request',),
        'seller_reports': ('seller_report',),
        'notifications': ('notification',),
        'orders': ('order', 'purchase', 'sale'),
        'users_activity': ('withdrawal',),
    }

    STATUS_FILTERS = (
        {'key': 'pending', 'label': 'Pending'},
        {'key': 'approved', 'label': 'Approved'},
        {'key': 'rejected', 'label': 'Rejected'},
        {'key': 'all', 'label': 'All Status'},
    )

    STATUS_MAP = {
        'pending': ('pending',),
        'approved': ('approved', 'accepted', 'completed', 'success', 'delivered', 'reviewed'),
        'rejected': ('rejected', 'expired', 'cancelled', 'refunded'),
        'all': None,
    }

    TYPE_LABELS = {
        'mission': 'Mission',
        'submission': 'Submission',
        'work_request': 'Work Request',
        'order': 'Service Request',
        'deposit': 'Deposit',
        'withdrawal': 'Withdrawal',
        'purchase': 'Purchase (Buy)',
        'sale': 'Sale (Sell)',
        'merch_product': 'Merch Product',
        'seller_request': 'Seller Request',
        'seller_report': 'Seller Report',
        'notification': 'Notification',
    }

    _last_user_sync: dict[int, datetime] = {}
    _last_admin_sync: datetime | None = None

    @staticmethod
    def ensure_history_schema() -> None:
        """Best-effort schema patching for existing DBs without migrations."""
        try:
            inspector = inspect(db.engine)
            table_names = set(inspector.get_table_names())
            statements: list[str] = []

            def add_column_if_missing(table: str, column: str, ddl: str) -> None:
                if table not in table_names:
                    return
                existing = {c['name'] for c in inspector.get_columns(table)}
                if column not in existing:
                    statements.append(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')

            # Backward-compatible source-table patching.
            add_column_if_missing('user_missions', 'created_at', 'TIMESTAMP')
            add_column_if_missing('user_missions', 'is_archived', 'BOOLEAN DEFAULT 0')
            add_column_if_missing('work_requests', 'created_at', 'TIMESTAMP')
            add_column_if_missing('work_requests', 'is_archived', 'BOOLEAN DEFAULT 0')
            add_column_if_missing('service_orders', 'created_at', 'TIMESTAMP')
            add_column_if_missing('service_orders', 'is_archived', 'BOOLEAN DEFAULT 0')
            add_column_if_missing('merch_orders', 'created_at', 'TIMESTAMP')
            add_column_if_missing('merch_orders', 'is_archived', 'BOOLEAN DEFAULT 0')
            add_column_if_missing('deposits', 'is_archived', 'BOOLEAN DEFAULT 0')
            add_column_if_missing('withdraw_requests', 'is_archived', 'BOOLEAN DEFAULT 0')

            for stmt in statements:
                db.session.execute(text(stmt))

            # Required unified history table.
            # Use database-agnostic syntax for auto-incrementing primary keys
            engine = db.engine
            is_postgres = 'postgresql' in str(engine.url).lower()
            
            if is_postgres:
                # PostgreSQL syntax
                auto_increment = "SERIAL PRIMARY KEY"
            else:
                # SQLite syntax
                auto_increment = "INTEGER PRIMARY KEY AUTOINCREMENT"
            
            db.session.execute(text(f"""
                CREATE TABLE IF NOT EXISTS history_entries (
                    id {auto_increment},
                    user_id INTEGER NOT NULL,
                    source_key VARCHAR(40) NOT NULL,
                    source_id INTEGER NOT NULL,
                    type VARCHAR(30) NOT NULL,
                    section VARCHAR(80),
                    status VARCHAR(30) NOT NULL DEFAULT 'pending',
                    is_archived BOOLEAN NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    summary TEXT,
                    link VARCHAR(255),
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT ux_history_entries_user_source UNIQUE (user_id, source_key, source_id, type),
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
            """))

            # Backfill created_at from legacy fields.
            # Only update if the source columns exist
            if table_names and 'user_missions' in table_names:
                existing_columns = {c['name'] for c in inspector.get_columns('user_missions')}
                if 'submission_time' in existing_columns:
                    db.session.execute(
                        text('UPDATE user_missions SET created_at = submission_time WHERE created_at IS NULL')
                    )
                db.session.execute(
                    text("UPDATE user_missions SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
                )
            
            if table_names and 'merch_orders' in table_names:
                existing_columns = {c['name'] for c in inspector.get_columns('merch_orders')}
                if 'purchased_at' in existing_columns:
                    db.session.execute(
                        text('UPDATE merch_orders SET created_at = purchased_at WHERE created_at IS NULL')
                    )
                db.session.execute(
                    text("UPDATE merch_orders SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
                )

            # Performance indexes.
            # Only create indexes if tables and columns exist
            if table_names and 'user_missions' in table_names:
                existing_columns = {c['name'] for c in inspector.get_columns('user_missions')}
                if 'user_id' in existing_columns and 'created_at' in existing_columns and 'is_archived' in existing_columns:
                    db.session.execute(
                        text('CREATE INDEX IF NOT EXISTS ix_user_missions_user_created_arch ON user_missions (user_id, created_at, is_archived)')
                    )
            
            if table_names and 'work_requests' in table_names:
                existing_columns = {c['name'] for c in inspector.get_columns('work_requests')}
                if 'user_id' in existing_columns and 'created_at' in existing_columns and 'is_archived' in existing_columns:
                    db.session.execute(
                        text('CREATE INDEX IF NOT EXISTS ix_work_requests_user_created_arch ON work_requests (user_id, created_at, is_archived)')
                    )
            
            if table_names and 'service_orders' in table_names:
                existing_columns = {c['name'] for c in inspector.get_columns('service_orders')}
                if 'user_id' in existing_columns and 'created_at' in existing_columns and 'is_archived' in existing_columns:
                    db.session.execute(
                        text('CREATE INDEX IF NOT EXISTS ix_service_orders_user_created_arch ON service_orders (user_id, created_at, is_archived)')
                    )
            
            if table_names and 'merch_orders' in table_names:
                existing_columns = {c['name'] for c in inspector.get_columns('merch_orders')}
                if 'user_id' in existing_columns and 'created_at' in existing_columns and 'is_archived' in existing_columns:
                    db.session.execute(
                        text('CREATE INDEX IF NOT EXISTS ix_merch_orders_user_created_arch ON merch_orders (user_id, created_at, is_archived)')
                    )
            
            if table_names and 'deposits' in table_names:
                existing_columns = {c['name'] for c in inspector.get_columns('deposits')}
                if 'user_id' in existing_columns and 'created_at' in existing_columns and 'is_archived' in existing_columns:
                    db.session.execute(
                        text('CREATE INDEX IF NOT EXISTS ix_deposits_user_created_arch ON deposits (user_id, created_at, is_archived)')
                    )
            
            if table_names and 'withdraw_requests' in table_names:
                existing_columns = {c['name'] for c in inspector.get_columns('withdraw_requests')}
                if 'user_id' in existing_columns and 'created_at' in existing_columns and 'is_archived' in existing_columns:
                    db.session.execute(
                        text('CREATE INDEX IF NOT EXISTS ix_withdraw_requests_user_created_arch ON withdraw_requests (user_id, created_at, is_archived)')
                    )
            
            # History table indexes
            if table_names and 'history_entries' in table_names:
                existing_columns = {c['name'] for c in inspector.get_columns('history_entries')}
                if 'user_id' in existing_columns and 'created_at' in existing_columns:
                    db.session.execute(
                        text('CREATE INDEX IF NOT EXISTS ix_history_entries_user_created ON history_entries (user_id, created_at DESC)')
                    )
                if 'user_id' in existing_columns and 'type' in existing_columns and 'is_archived' in existing_columns and 'created_at' in existing_columns:
                    db.session.execute(
                        text('CREATE INDEX IF NOT EXISTS ix_history_entries_user_type_arch ON history_entries (user_id, type, is_archived, created_at DESC)')
                    )
                if 'type' in existing_columns and 'status' in existing_columns and 'is_archived' in existing_columns and 'created_at' in existing_columns:
                    db.session.execute(
                        text('CREATE INDEX IF NOT EXISTS ix_history_entries_type_status_arch ON history_entries (type, status, is_archived, created_at DESC)')
                    )

            db.session.commit()
        except Exception as e:
            # If schema setup fails, rollback and continue - don't break the app startup
            db.session.rollback()
            print(f"Warning: Schema setup failed: {e}. Continuing with app startup.")

    @staticmethod
    def cutoff_datetime() -> datetime:
        return utc_now() - timedelta(days=HistoryService.RETENTION_DAYS)

    @staticmethod
    def normalize_user_filter(filter_key: str | None) -> str:
        key = (filter_key or 'all').strip().lower()
        return key if key in HistoryService.USER_FILTER_TYPES else 'all'

    @staticmethod
    def normalize_admin_filter(filter_key: str | None) -> str:
        key = (filter_key or 'all').strip().lower()
        return key if key in HistoryService.ADMIN_FILTER_TYPES else 'all'

    @staticmethod
    def normalize_status_filter(status_key: str | None) -> str:
        key = (status_key or 'pending').strip().lower()
        return key if key in HistoryService.STATUS_MAP else 'pending'

    @staticmethod
    def archive_due_items(user_id: int | None = None) -> int:
        """Archive source rows and history rows older than retention window."""
        cutoff = HistoryService.cutoff_datetime()
        changed = 0

        for cfg in HistoryService.SOURCE_CONFIG:
            model = cfg['model']
            created_col = getattr(model, cfg['created_field'])
            query = model.query.filter(model.is_archived.is_(False), created_col < cutoff)
            if user_id is not None:
                query = query.filter(model.user_id == user_id)
            changed += query.update({'is_archived': True}, synchronize_session=False)

        history_query = HistoryEntry.query.filter(
            HistoryEntry.is_archived.is_(False),
            HistoryEntry.created_at < cutoff,
        )
        if user_id is not None:
            history_query = history_query.filter(HistoryEntry.user_id == user_id)
        changed += history_query.update({'is_archived': True}, synchronize_session=False)

        if changed:
            db.session.commit()
        return changed

    @staticmethod
    def mark_archived_if_terminal(record, source_key: str) -> None:
        """Mark source row archived immediately when it reaches terminal state."""
        terminal = HistoryService.TERMINAL_STATUSES.get(source_key, set())
        status = (getattr(record, 'status', '') or '').lower()
        if status in terminal:
            record.is_archived = True

    @staticmethod
    def sync_history_entries(user_id: int | None = None, force: bool = False) -> int:
        """Sync denormalized history entries from source tables."""
        now = utc_now()
        if not force:
            if user_id is not None:
                last = HistoryService._last_user_sync.get(user_id)
                if last and (now - last).total_seconds() < HistoryService.SYNC_COOLDOWN_SECONDS:
                    return 0
            else:
                if HistoryService._last_admin_sync and (now - HistoryService._last_admin_sync).total_seconds() < HistoryService.SYNC_COOLDOWN_SECONDS:
                    return 0

        HistoryService.archive_due_items(user_id=user_id)

        payloads: list[dict] = []
        payloads.extend(HistoryService._build_submission_entries(user_id=user_id))
        payloads.extend(HistoryService._build_work_request_entries(user_id=user_id))
        payloads.extend(HistoryService._build_order_entries(user_id=user_id))
        payloads.extend(HistoryService._build_deposit_entries(user_id=user_id))
        payloads.extend(HistoryService._build_withdrawal_entries(user_id=user_id))
        payloads.extend(HistoryService._build_purchase_entries(user_id=user_id))
        payloads.extend(HistoryService._build_sale_entries(user_id=user_id))
        if user_id is None:
            payloads.extend(HistoryService._build_mission_entries())
            payloads.extend(HistoryService._build_merch_product_entries())
            payloads.extend(HistoryService._build_seller_request_entries())
            payloads.extend(HistoryService._build_seller_report_entries())
            payloads.extend(HistoryService._build_notification_entries())

        changed = HistoryService._upsert_payloads(payloads)
        if changed:
            db.session.commit()

        if user_id is not None:
            HistoryService._last_user_sync[user_id] = now
        else:
            HistoryService._last_admin_sync = now

        return changed

    @staticmethod
    def get_user_recent_history(user_id: int, filter_key: str = 'all', limit: int = ACTIVE_LIMIT) -> list[dict]:
        HistoryService.sync_history_entries(user_id=user_id)
        normalized = HistoryService.normalize_user_filter(filter_key)
        query = HistoryEntry.query.filter(
            HistoryEntry.user_id == user_id,
            HistoryEntry.is_archived.is_(False),
        )

        types = HistoryService.USER_FILTER_TYPES.get(normalized)
        if types:
            query = query.filter(HistoryEntry.type.in_(types))

        rows = query.order_by(HistoryEntry.created_at.desc()).limit(max(int(limit or 10), 1)).all()
        return [HistoryService._to_item(row) for row in rows]

    @staticmethod
    def get_user_old_history(user_id: int, filter_key: str = 'all', page: int = 1, per_page: int = 20) -> dict:
        HistoryService.sync_history_entries(user_id=user_id)
        normalized = HistoryService.normalize_user_filter(filter_key)

        query = HistoryEntry.query.filter(
            HistoryEntry.user_id == user_id,
            HistoryEntry.is_archived.is_(True),
        )
        types = HistoryService.USER_FILTER_TYPES.get(normalized)
        if types:
            query = query.filter(HistoryEntry.type.in_(types))

        paginated = query.order_by(HistoryEntry.created_at.desc()).paginate(
            page=max(int(page or 1), 1),
            per_page=max(min(int(per_page or 20), 100), 1),
            error_out=False,
        )
        return {
            'items': [HistoryService._to_item(row) for row in paginated.items],
            'page': paginated.page,
            'per_page': paginated.per_page,
            'total': paginated.total,
            'pages': paginated.pages,
            'has_prev': paginated.has_prev,
            'has_next': paginated.has_next,
            'prev_num': paginated.prev_num,
            'next_num': paginated.next_num,
        }

    @staticmethod
    def get_admin_history(
        filter_key: str = 'all',
        status_key: str = 'pending',
        page: int = 1,
        per_page: int = 30,
        include_old: bool = False,
    ) -> dict:
        HistoryService.sync_history_entries(user_id=None)
        normalized_filter = HistoryService.normalize_admin_filter(filter_key)
        normalized_status = HistoryService.normalize_status_filter(status_key)

        query = HistoryEntry.query
        if include_old:
            query = query.filter(
                or_(
                    HistoryEntry.is_archived.is_(True),
                    HistoryEntry.status != 'pending',
                )
            )
        else:
            query = query.filter(HistoryEntry.is_archived.is_(False))

        types = HistoryService.ADMIN_FILTER_TYPES.get(normalized_filter)
        if types:
            query = query.filter(HistoryEntry.type.in_(types))

        status_group = HistoryService.STATUS_MAP.get(normalized_status)
        if status_group:
            query = query.filter(HistoryEntry.status.in_(status_group))

        paginated = query.order_by(HistoryEntry.created_at.desc()).paginate(
            page=max(int(page or 1), 1),
            per_page=max(min(int(per_page or 30), 100), 1),
            error_out=False,
        )

        return {
            'items': [HistoryService._to_item(row) for row in paginated.items],
            'page': paginated.page,
            'per_page': paginated.per_page,
            'total': paginated.total,
            'pages': paginated.pages,
            'has_prev': paginated.has_prev,
            'has_next': paginated.has_next,
            'prev_num': paginated.prev_num,
            'next_num': paginated.next_num,
        }

    @staticmethod
    def get_active_history(user_id: int, limit_per_type: int | None = None) -> list[dict]:
        """Backward-compatible wrapper now returning latest 10 total."""
        limit = int(limit_per_type or HistoryService.ACTIVE_LIMIT)
        return HistoryService.get_user_recent_history(user_id=user_id, filter_key='all', limit=limit)

    @staticmethod
    def get_old_history(user_id: int, page: int = 1, per_page: int = 20) -> dict:
        return HistoryService.get_user_old_history(user_id=user_id, filter_key='all', page=page, per_page=per_page)

    @staticmethod
    def get_admin_old_history(page: int = 1, per_page: int = 30) -> dict:
        return HistoryService.get_admin_history(
            filter_key='all',
            status_key='all',
            page=page,
            per_page=per_page,
            include_old=True,
        )

    @staticmethod
    def _build_submission_entries(user_id: int | None) -> list[dict]:
        query = UserMission.query
        if user_id is not None:
            query = query.filter(UserMission.user_id == user_id)
        rows = query.order_by(UserMission.created_at.desc()).all()

        payloads = []
        for row in rows:
            payloads.append(
                HistoryService._entry_payload(
                    user_id=row.user_id,
                    source_key='user_missions',
                    source_id=row.id,
                    entry_type='submission',
                    section='Submissions',
                    status=(row.status or 'pending'),
                    created_at=HistoryService._record_created_at(row),
                    source_archived=bool(row.is_archived),
                    summary=row.mission_title or f'Mission #{row.mission_id}',
                    link=f'/missions/{row.mission_id}',
                )
            )
        return payloads

    @staticmethod
    def _build_work_request_entries(user_id: int | None) -> list[dict]:
        query = WorkRequest.query
        if user_id is not None:
            query = query.filter(WorkRequest.user_id == user_id)
        rows = query.order_by(WorkRequest.created_at.desc()).all()

        payloads = []
        for row in rows:
            snippet = (row.message or '').strip()
            summary = snippet[:80] if snippet else f'Work Request #{row.id}'
            payloads.append(
                HistoryService._entry_payload(
                    user_id=row.user_id,
                    source_key='work_requests',
                    source_id=row.id,
                    entry_type='work_request',
                    section='Work Requests',
                    status=(row.status or 'pending'),
                    created_at=HistoryService._record_created_at(row),
                    source_archived=bool(row.is_archived),
                    summary=summary,
                    link=f'/work/requests/{row.id}',
                )
            )
        return payloads

    @staticmethod
    def _build_order_entries(user_id: int | None) -> list[dict]:
        query = ServiceOrder.query
        if user_id is not None:
            query = query.filter(ServiceOrder.user_id == user_id)
        rows = query.order_by(ServiceOrder.created_at.desc()).all()

        payloads = []
        for row in rows:
            summary = f'{row.category} / {row.service} (x{row.quantity})'
            payloads.append(
                HistoryService._entry_payload(
                    user_id=row.user_id,
                    source_key='service_orders',
                    source_id=row.id,
                    entry_type='order',
                    section='Orders',
                    status=(row.status or 'pending'),
                    created_at=HistoryService._record_created_at(row),
                    source_archived=bool(row.is_archived),
                    summary=summary,
                    link=f'/work/orders/{row.id}',
                )
            )
        return payloads

    @staticmethod
    def _build_deposit_entries(user_id: int | None) -> list[dict]:
        query = Deposit.query
        if user_id is not None:
            query = query.filter(Deposit.user_id == user_id)
        rows = query.order_by(Deposit.created_at.desc()).all()

        payloads = []
        for row in rows:
            amount = _safe_float(row.usdt_amount)
            payloads.append(
                HistoryService._entry_payload(
                    user_id=row.user_id,
                    source_key='deposits',
                    source_id=row.id,
                    entry_type='deposit',
                    section='Deposits',
                    status=(row.status or 'pending'),
                    created_at=HistoryService._record_created_at(row),
                    source_archived=bool(row.is_archived),
                    summary=f'{row.coin_type} {amount:.6f}',
                    link=f'/deposit/{row.id}',
                )
            )
        return payloads

    @staticmethod
    def _build_withdrawal_entries(user_id: int | None) -> list[dict]:
        query = WithdrawRequest.query
        if user_id is not None:
            query = query.filter(WithdrawRequest.user_id == user_id)
        rows = query.order_by(WithdrawRequest.created_at.desc()).all()

        payloads = []
        for row in rows:
            payloads.append(
                HistoryService._entry_payload(
                    user_id=row.user_id,
                    source_key='withdraw_requests',
                    source_id=row.id,
                    entry_type='withdrawal',
                    section='Withdrawals',
                    status=(row.status or 'pending'),
                    created_at=HistoryService._record_created_at(row),
                    source_archived=bool(row.is_archived),
                    summary=f'{int(row.amount or 0):,} TNNO',
                    link='/work/withdraw',
                )
            )
        return payloads

    @staticmethod
    def _build_purchase_entries(user_id: int | None) -> list[dict]:
        query = db.session.query(MerchOrder, Product).outerjoin(Product, Product.id == MerchOrder.product_id)
        if user_id is not None:
            query = query.filter(MerchOrder.user_id == user_id)

        rows = query.order_by(MerchOrder.created_at.desc()).all()
        payloads = []
        for order, product in rows:
            name = product.name if product else f'Product #{order.product_id}'
            payloads.append(
                HistoryService._entry_payload(
                    user_id=order.user_id,
                    source_key='merch_orders',
                    source_id=order.id,
                    entry_type='purchase',
                    section='Purchases',
                    status=(order.status or 'completed'),
                    created_at=HistoryService._record_created_at(order),
                    source_archived=bool(order.is_archived),
                    summary=f'{name} (x{order.quantity})',
                    link='/store/my-orders',
                )
            )
        return payloads

    @staticmethod
    def _build_sale_entries(user_id: int | None) -> list[dict]:
        query = (
            db.session.query(MerchOrder, Product)
            .join(Product, Product.id == MerchOrder.product_id)
            .filter(Product.seller_id.isnot(None))
        )
        if user_id is not None:
            query = query.filter(Product.seller_id == user_id)

        rows = query.order_by(MerchOrder.created_at.desc()).all()
        payloads = []
        for order, product in rows:
            seller_id = product.seller_id if product else None
            if not seller_id:
                continue
            name = product.name if product else f'Product #{order.product_id}'
            payloads.append(
                HistoryService._entry_payload(
                    user_id=seller_id,
                    source_key='merch_orders_sale',
                    source_id=order.id,
                    entry_type='sale',
                    section='Sales',
                    status=(order.status or 'completed'),
                    created_at=HistoryService._record_created_at(order),
                    source_archived=bool(order.is_archived),
                    summary=f'{name} sold (x{order.quantity})',
                    link='/store/admin/sales',
                )
            )
        return payloads

    @staticmethod
    def _build_mission_entries() -> list[dict]:
        rows = UserMission.query.order_by(UserMission.created_at.desc()).all()
        payloads = []
        for row in rows:
            payloads.append(
                HistoryService._entry_payload(
                    user_id=row.user_id,
                    source_key='missions_activity',
                    source_id=row.id,
                    entry_type='mission',
                    section='Missions',
                    status=(row.status or 'pending'),
                    created_at=HistoryService._record_created_at(row),
                    source_archived=bool(row.is_archived),
                    summary=row.mission_title or f'Mission #{row.mission_id}',
                    link=f'/missions/{row.mission_id}',
                )
            )
        return payloads

    @staticmethod
    def _build_merch_product_entries() -> list[dict]:
        rows = Product.query.filter(Product.seller_id.isnot(None)).order_by(Product.created_at.desc()).all()
        payloads = []
        for row in rows:
            payloads.append(
                HistoryService._entry_payload(
                    user_id=row.seller_id,
                    source_key='products',
                    source_id=row.id,
                    entry_type='merch_product',
                    section='Merch Products',
                    status=('active' if row.is_active else 'inactive'),
                    created_at=row.created_at,
                    source_archived=not bool(row.is_active),
                    summary=f'{row.name} ({(row.product_type or "digital").title()})',
                    link=f'/store/admin/edit/{row.id}',
                )
            )
        return payloads

    @staticmethod
    def _build_seller_request_entries() -> list[dict]:
        rows = SellerRequest.query.order_by(SellerRequest.created_at.desc()).all()
        payloads = []
        for row in rows:
            summary = f'{row.real_name} - {row.country}, {row.city}'
            payloads.append(
                HistoryService._entry_payload(
                    user_id=row.user_id,
                    source_key='seller_requests',
                    source_id=row.id,
                    entry_type='seller_request',
                    section='Seller Requests',
                    status=(row.status or 'pending'),
                    created_at=row.created_at,
                    source_archived=False,
                    summary=summary,
                    link=f'/admin/seller-requests/{row.id}',
                )
            )
        return payloads

    @staticmethod
    def _build_seller_report_entries() -> list[dict]:
        rows = SellerReport.query.order_by(SellerReport.created_at.desc()).all()
        payloads = []
        for row in rows:
            summary = (row.message or '').strip()[:90] or f'Seller Report #{row.id}'
            payloads.append(
                HistoryService._entry_payload(
                    user_id=row.reporter_id,
                    source_key='seller_reports',
                    source_id=row.id,
                    entry_type='seller_report',
                    section='Seller Reports',
                    status=(row.status or 'pending'),
                    created_at=row.created_at,
                    source_archived=False,
                    summary=summary,
                    link='/admin/seller-reports',
                )
            )
        return payloads

    @staticmethod
    def _build_notification_entries() -> list[dict]:
        rows = UserNotification.query.order_by(UserNotification.created_at.desc()).all()
        payloads = []
        for row in rows:
            summary = (row.message or '').strip()[:90] or f'Notification #{row.id}'
            payloads.append(
                HistoryService._entry_payload(
                    user_id=row.user_id,
                    source_key='notifications',
                    source_id=row.id,
                    entry_type='notification',
                    section='Notifications',
                    status=('read' if row.read_at else 'unread'),
                    created_at=row.created_at,
                    source_archived=False,
                    summary=summary,
                    link='/admin/notifications',
                )
            )
        return payloads

    @staticmethod
    def _entry_payload(
        user_id: int,
        source_key: str,
        source_id: int,
        entry_type: str,
        section: str,
        status: str,
        created_at,
        source_archived: bool,
        summary: str,
        link: str | None,
    ) -> dict:
        created = created_at or utc_now()
        archived = bool(source_archived) or created < HistoryService.cutoff_datetime()
        return {
            'user_id': int(user_id),
            'source_key': source_key,
            'source_id': int(source_id),
            'type': entry_type,
            'section': section,
            'status': (status or 'pending').strip().lower(),
            'is_archived': archived,
            'created_at': created,
            'summary': summary,
            'link': link,
        }

    @staticmethod
    def _upsert_payloads(payloads: list[dict]) -> int:
        if not payloads:
            return 0

        changed = 0
        grouped: dict[tuple[int, str, str], list[dict]] = defaultdict(list)
        for payload in payloads:
            grouped[(payload['user_id'], payload['source_key'], payload['type'])].append(payload)

        for (user_id, source_key, entry_type), rows in grouped.items():
            source_ids = [row['source_id'] for row in rows]
            existing_rows = (
                HistoryEntry.query
                .filter(
                    HistoryEntry.user_id == user_id,
                    HistoryEntry.source_key == source_key,
                    HistoryEntry.type == entry_type,
                    HistoryEntry.source_id.in_(source_ids),
                )
                .all()
            )
            existing_map = {row.source_id: row for row in existing_rows}

            for payload in rows:
                existing = existing_map.get(payload['source_id'])
                if existing is None:
                    db.session.add(HistoryEntry(**payload))
                    changed += 1
                    continue

                if (
                    existing.status != payload['status']
                    or bool(existing.is_archived) != bool(payload['is_archived'])
                    or existing.created_at != payload['created_at']
                    or (existing.summary or '') != (payload['summary'] or '')
                    or (existing.link or '') != (payload['link'] or '')
                    or (existing.section or '') != (payload['section'] or '')
                ):
                    existing.status = payload['status']
                    existing.is_archived = payload['is_archived']
                    existing.created_at = payload['created_at']
                    existing.summary = payload['summary']
                    existing.link = payload['link']
                    existing.section = payload['section']
                    existing.updated_at = utc_now()
                    changed += 1

        return changed

    @staticmethod
    def _record_created_at(record):
        dt = getattr(record, 'created_at', None)
        if dt:
            return dt
        return getattr(record, 'submission_time', None) or getattr(record, 'purchased_at', None)

    @staticmethod
    def _to_item(entry: HistoryEntry) -> dict:
        return {
            'id': entry.id,
            'user_id': entry.user_id,
            'user_name': entry.user.username if entry.user else f'User #{entry.user_id}',
            'source_key': entry.source_key,
            'source_label': entry.section or HistoryService.TYPE_LABELS.get(entry.type, entry.type.title()),
            'type': entry.type,
            'type_label': HistoryService.TYPE_LABELS.get(entry.type, entry.type.title()),
            'status': entry.status,
            'created_at': entry.created_at,
            'summary': entry.summary,
            'link': entry.link,
            'is_archived': bool(entry.is_archived),
        }


def _safe_float(value) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0
