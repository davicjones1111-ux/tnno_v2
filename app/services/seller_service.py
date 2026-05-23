"""
Seller Service
Seller request and subscription helpers
"""
from __future__ import annotations

from datetime import datetime, timedelta
from sqlalchemy import inspect, text

from app.datetime_utils import utc_now
from app.extensions import db


SELLER_PLANS = {
    '1m': {'label': '1 Month', 'months': 1, 'cost': 20000},
    '3m': {'label': '3 Months', 'months': 3, 'cost': 50000},
    '12m': {'label': '1 Year', 'months': 12, 'cost': 200000},
}


class SellerService:
    """Helpers for seller subscription logic and schema upgrades."""

    @staticmethod
    def ensure_seller_schema():
        """Best-effort schema patching for seller-related fields."""
        inspector = inspect(db.engine)
        if 'users' not in inspector.get_table_names():
            return

        user_cols = {col['name'] for col in inspector.get_columns('users')}
        alter_statements = []

        engine_url = str(db.engine.url).lower()
        is_postgres = 'postgresql' in engine_url

        if 'seller_expires_at' not in user_cols:
            alter_statements.append('ALTER TABLE users ADD COLUMN seller_expires_at TIMESTAMP')
        if 'seller_reminder_sent_at' not in user_cols:
            alter_statements.append('ALTER TABLE users ADD COLUMN seller_reminder_sent_at TIMESTAMP')
        if 'seller_sales_seen_at' not in user_cols:
            alter_statements.append('ALTER TABLE users ADD COLUMN seller_sales_seen_at TIMESTAMP')
        if 'seller_cover_photo' not in user_cols:
            alter_statements.append("ALTER TABLE users ADD COLUMN seller_cover_photo VARCHAR(255) DEFAULT ''")

        id_column = 'SERIAL PRIMARY KEY' if is_postgres else 'INTEGER PRIMARY KEY AUTOINCREMENT'

        # Create seller_ratings table if missing (SQLite-friendly)
        if 'seller_ratings' not in inspector.get_table_names():
            alter_statements.append(
                'CREATE TABLE seller_ratings ('
                f'id {id_column}, '
                'seller_id INTEGER NOT NULL, '
                'rater_id INTEGER NOT NULL, '
                'rating INTEGER NOT NULL, '
                'created_at TIMESTAMP, '
                'updated_at TIMESTAMP, '
                'CONSTRAINT ux_seller_ratings_seller_rater UNIQUE (seller_id, rater_id)'
                ')'
            )
            alter_statements.append('CREATE INDEX IF NOT EXISTS ix_seller_ratings_seller_id ON seller_ratings (seller_id)')
            alter_statements.append('CREATE INDEX IF NOT EXISTS ix_seller_ratings_rater_id ON seller_ratings (rater_id)')

        # Create seller_reports table if missing
        if 'seller_reports' not in inspector.get_table_names():
            alter_statements.append(
                'CREATE TABLE seller_reports ('
                f'id {id_column}, '
                'seller_id INTEGER NOT NULL, '
                'reporter_id INTEGER NOT NULL, '
                'message TEXT NOT NULL, '
                'evidence_path VARCHAR(255), '
                "status VARCHAR(20) DEFAULT 'pending', "
                'created_at TIMESTAMP, '
                'reviewed_at TIMESTAMP, '
                'reviewed_by INTEGER'
                ')'
            )
            alter_statements.append('CREATE INDEX IF NOT EXISTS ix_seller_reports_seller_id ON seller_reports (seller_id)')
            alter_statements.append('CREATE INDEX IF NOT EXISTS ix_seller_reports_reporter_id ON seller_reports (reporter_id)')

        # Seller request columns (for existing databases)
        if 'seller_requests' in inspector.get_table_names():
            req_cols = {col['name'] for col in inspector.get_columns('seller_requests')}
            if 'plan_key' not in req_cols:
                alter_statements.append('ALTER TABLE seller_requests ADD COLUMN plan_key VARCHAR(10)')
            if 'plan_months' not in req_cols:
                alter_statements.append('ALTER TABLE seller_requests ADD COLUMN plan_months INTEGER DEFAULT 1')
            if 'plan_cost' not in req_cols:
                alter_statements.append('ALTER TABLE seller_requests ADD COLUMN plan_cost INTEGER DEFAULT 0')

        for statement in alter_statements:
            db.session.execute(text(statement))

        if alter_statements:
            db.session.commit()

    @staticmethod
    def compute_new_expiry(current_expires_at: datetime | None, months: int) -> datetime:
        """Return new expiration timestamp using 30-day months."""
        now = utc_now()
        base = current_expires_at if current_expires_at and current_expires_at > now else now
        return base + timedelta(days=30 * months)
