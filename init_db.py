"""
Initialize database schema and runtime indexes.
Run this once per environment before starting scaled web workers.
"""
from __future__ import annotations

import os

from app import create_app, ensure_runtime_indexes
from app.extensions import db
from app.services.deposit_service import DepositService


def main():
    app = create_app(os.environ.get('FLASK_ENV', 'production'))
    with app.app_context():
        db.create_all()
        DepositService.ensure_deposit_schema()
        ensure_runtime_indexes()
    print('Database initialization completed.')


if __name__ == '__main__':
    main()
