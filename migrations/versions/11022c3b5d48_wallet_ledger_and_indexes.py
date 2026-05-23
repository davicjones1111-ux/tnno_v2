"""wallet ledger and indexes

Revision ID: 11022c3b5d48
Revises:
Create Date: 2026-04-22 10:35:44.313237

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


# revision identifiers, used by Alembic.
revision = '11022c3b5d48'
down_revision = None
branch_labels = None
depends_on = None


def _table_names(bind):
    return set(inspect(bind).get_table_names())


def _column_names(bind, table_name):
    inspector = inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {column['name'] for column in inspector.get_columns(table_name)}


def upgrade():
    bind = op.get_bind()
    table_names = _table_names(bind)

    if 'wallet_transactions' not in table_names:
        op.create_table(
            'wallet_transactions',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('transaction_type', sa.String(length=40), nullable=False),
            sa.Column('amount', sa.Integer(), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='completed'),
            sa.Column('balance_before', sa.Float(), nullable=True),
            sa.Column('balance_after', sa.Float(), nullable=True),
            sa.Column('reference_type', sa.String(length=40), nullable=True),
            sa.Column('reference_id', sa.Integer(), nullable=True),
            sa.Column('details', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
        )

    # Safe, additive indexes for hot paths.
    index_statements = [
        'CREATE INDEX IF NOT EXISTS ix_wallet_transactions_user_created ON wallet_transactions (user_id, created_at)',
        'CREATE INDEX IF NOT EXISTS ix_users_role_created_at ON users (role, created_at)',
        'CREATE INDEX IF NOT EXISTS ix_posts_parent_created_at ON posts (parent_id, created_at)',
        'CREATE INDEX IF NOT EXISTS ix_posts_user_created_at ON posts (user_id, created_at)',
        'CREATE INDEX IF NOT EXISTS ix_user_notifications_user_read_created ON user_notifications (user_id, read_at, created_at)',
        'CREATE INDEX IF NOT EXISTS ix_seller_notifications_seller_read_created ON seller_notifications (seller_id, is_read, created_at)',
        'CREATE INDEX IF NOT EXISTS ix_seller_chat_buyer_updated_at ON seller_chat_conversations (buyer_id, updated_at)',
        'CREATE INDEX IF NOT EXISTS ix_seller_chat_seller_updated_at ON seller_chat_conversations (seller_id, updated_at)',
        'CREATE INDEX IF NOT EXISTS ix_seller_chat_messages_conversation_created ON seller_chat_messages (conversation_id, created_at)',
        'CREATE INDEX IF NOT EXISTS ix_seller_chat_messages_conversation_read ON seller_chat_messages (conversation_id, is_read)',
        'CREATE INDEX IF NOT EXISTS ix_products_active_created_type ON products (is_active, created_at, product_type)',
        'CREATE INDEX IF NOT EXISTS ix_products_seller_active_created ON products (seller_id, is_active, created_at)',
        'CREATE INDEX IF NOT EXISTS ix_merch_orders_user_created_at ON merch_orders (user_id, created_at)',
        'CREATE INDEX IF NOT EXISTS ix_merch_orders_product_created_at ON merch_orders (product_id, created_at)',
        'CREATE INDEX IF NOT EXISTS ix_withdraw_requests_user_status_created ON withdraw_requests (user_id, status, created_at)',
    ]
    for statement in index_statements:
        bind.execute(text(statement))

    # Add updated_at index if the column exists in older databases.
    if 'updated_at' in _column_names(bind, 'users'):
        bind.execute(text('CREATE INDEX IF NOT EXISTS ix_users_updated_at ON users (updated_at)'))


def downgrade():
    bind = op.get_bind()
    drop_index_statements = [
        'DROP INDEX IF EXISTS ix_users_updated_at',
        'DROP INDEX IF EXISTS ix_withdraw_requests_user_status_created',
        'DROP INDEX IF EXISTS ix_merch_orders_product_created_at',
        'DROP INDEX IF EXISTS ix_merch_orders_user_created_at',
        'DROP INDEX IF EXISTS ix_products_seller_active_created',
        'DROP INDEX IF EXISTS ix_products_active_created_type',
        'DROP INDEX IF EXISTS ix_seller_chat_messages_conversation_read',
        'DROP INDEX IF EXISTS ix_seller_chat_messages_conversation_created',
        'DROP INDEX IF EXISTS ix_seller_chat_seller_updated_at',
        'DROP INDEX IF EXISTS ix_seller_chat_buyer_updated_at',
        'DROP INDEX IF EXISTS ix_seller_notifications_seller_read_created',
        'DROP INDEX IF EXISTS ix_user_notifications_user_read_created',
        'DROP INDEX IF EXISTS ix_posts_user_created_at',
        'DROP INDEX IF EXISTS ix_posts_parent_created_at',
        'DROP INDEX IF EXISTS ix_users_role_created_at',
        'DROP INDEX IF EXISTS ix_wallet_transactions_user_created',
    ]
    for statement in drop_index_statements:
        bind.execute(text(statement))

    if 'wallet_transactions' in _table_names(bind):
        op.drop_table('wallet_transactions')
