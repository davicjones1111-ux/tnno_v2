"""
Notification Service
Schema helpers for user notifications
"""
from __future__ import annotations

from sqlalchemy import inspect, text
from app.extensions import db


class NotificationService:
    """Helpers for user notifications schema."""

    @staticmethod
    def ensure_notification_schema():
        inspector = inspect(db.engine)
        engine_url = str(db.engine.url).lower()
        is_postgres = 'postgresql' in engine_url
        id_column = 'SERIAL PRIMARY KEY' if is_postgres else 'INTEGER PRIMARY KEY AUTOINCREMENT'
        if 'user_notifications' not in inspector.get_table_names():
            db.session.execute(text(
                'CREATE TABLE user_notifications ('
                f'id {id_column}, '
                'user_id INTEGER NOT NULL, '
                'message TEXT NOT NULL, '
                'attachment_path VARCHAR(255), '
                'created_at TIMESTAMP, '
                'read_at TIMESTAMP, '
                'sent_by INTEGER'
                ')'
            ))
            db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_user_notifications_user_id ON user_notifications (user_id)'))
            db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_user_notifications_created_at ON user_notifications (created_at)'))
            db.session.commit()
