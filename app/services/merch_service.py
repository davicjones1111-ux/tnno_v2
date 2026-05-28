"""
Merch Service
Schema helpers for merch store upgrades.
"""
from sqlalchemy import inspect, text

from app.extensions import db


class MerchService:
    """Helpers for merch store schema upgrades."""

    @staticmethod
    def ensure_merch_schema():
        """Best-effort schema patching for merch-related fields."""
        inspector = inspect(db.engine)
        table_names = set(inspector.get_table_names())
        engine_url = str(db.engine.url).lower()
        is_postgres = 'postgresql' in engine_url
        id_column = 'SERIAL PRIMARY KEY' if is_postgres else 'INTEGER PRIMARY KEY AUTOINCREMENT'

        alter_statements = []

        if 'products' in table_names:
            product_cols = {col['name'] for col in inspector.get_columns('products')}
            if 'product_type' not in product_cols:
                alter_statements.append("ALTER TABLE products ADD COLUMN product_type VARCHAR(20) DEFAULT 'digital'")
            if 'contact_link' not in product_cols:
                alter_statements.append('ALTER TABLE products ADD COLUMN contact_link VARCHAR(255)')
            if 'physical_quantity' not in product_cols:
                alter_statements.append('ALTER TABLE products ADD COLUMN physical_quantity INTEGER DEFAULT 0')

        if 'merch_orders' in table_names:
            order_cols = {col['name'] for col in inspector.get_columns('merch_orders')}
            if 'product_type' not in order_cols:
                alter_statements.append("ALTER TABLE merch_orders ADD COLUMN product_type VARCHAR(20) DEFAULT 'digital'")
            if 'shipping_name' not in order_cols:
                alter_statements.append('ALTER TABLE merch_orders ADD COLUMN shipping_name VARCHAR(120)')
            if 'shipping_country' not in order_cols:
                alter_statements.append('ALTER TABLE merch_orders ADD COLUMN shipping_country VARCHAR(120)')
            if 'shipping_city' not in order_cols:
                alter_statements.append('ALTER TABLE merch_orders ADD COLUMN shipping_city VARCHAR(120)')
            if 'shipping_phone' not in order_cols:
                alter_statements.append('ALTER TABLE merch_orders ADD COLUMN shipping_phone VARCHAR(40)')
            if 'shipping_lat' not in order_cols:
                alter_statements.append('ALTER TABLE merch_orders ADD COLUMN shipping_lat FLOAT')
            if 'shipping_lng' not in order_cols:
                alter_statements.append('ALTER TABLE merch_orders ADD COLUMN shipping_lng FLOAT')
            if 'shipping_location_text' not in order_cols:
                alter_statements.append('ALTER TABLE merch_orders ADD COLUMN shipping_location_text VARCHAR(255)')
            if 'delivery_eta' not in order_cols:
                alter_statements.append('ALTER TABLE merch_orders ADD COLUMN delivery_eta TIMESTAMP')
            if 'delivered_at' not in order_cols:
                alter_statements.append('ALTER TABLE merch_orders ADD COLUMN delivered_at TIMESTAMP')
            if 'refunded_at' not in order_cols:
                alter_statements.append('ALTER TABLE merch_orders ADD COLUMN refunded_at TIMESTAMP')

        if 'product_files' in table_names:
            file_cols = {col['name'] for col in inspector.get_columns('product_files')}
            if 'file_name' not in file_cols:
                alter_statements.append('ALTER TABLE product_files ADD COLUMN file_name VARCHAR(255)')
            if 'file_type' not in file_cols:
                alter_statements.append('ALTER TABLE product_files ADD COLUMN file_type VARCHAR(120)')
            if 'mime_type' not in file_cols:
                alter_statements.append('ALTER TABLE product_files ADD COLUMN mime_type VARCHAR(120)')
            if 'file_size' not in file_cols:
                alter_statements.append('ALTER TABLE product_files ADD COLUMN file_size BIGINT')
            if 'storage_provider' not in file_cols:
                alter_statements.append("ALTER TABLE product_files ADD COLUMN storage_provider VARCHAR(30) DEFAULT 's3'")
            if 'storage_key' not in file_cols:
                alter_statements.append('ALTER TABLE product_files ADD COLUMN storage_key VARCHAR(512)')
            if 'storage_url' not in file_cols:
                alter_statements.append('ALTER TABLE product_files ADD COLUMN storage_url VARCHAR(1024)')
            if 'folder_path' not in file_cols:
                alter_statements.append('ALTER TABLE product_files ADD COLUMN folder_path VARCHAR(255)')
            if 'upload_status' not in file_cols:
                alter_statements.append("ALTER TABLE product_files ADD COLUMN upload_status VARCHAR(30) DEFAULT 'ready'")
            if 'checksum' not in file_cols:
                alter_statements.append('ALTER TABLE product_files ADD COLUMN checksum VARCHAR(128)')
            if 'multipart_upload_id' not in file_cols:
                alter_statements.append('ALTER TABLE product_files ADD COLUMN multipart_upload_id VARCHAR(255)')
            if 'part_count' not in file_cols:
                alter_statements.append('ALTER TABLE product_files ADD COLUMN part_count INTEGER DEFAULT 0')
            if 'upload_session_id' not in file_cols:
                alter_statements.append('ALTER TABLE product_files ADD COLUMN upload_session_id INTEGER')

        if 'upload_sessions' not in table_names:
            alter_statements.append(
                'CREATE TABLE upload_sessions ('
                f'id {id_column}, '
                'product_id INTEGER NOT NULL, '
                'seller_id INTEGER NOT NULL, '
                'total_files INTEGER NOT NULL DEFAULT 0, '
                'uploaded_files INTEGER NOT NULL DEFAULT 0, '
                'total_bytes BIGINT NOT NULL DEFAULT 0, '
                "status VARCHAR(30) DEFAULT 'initiated', "
                'expires_at TIMESTAMP, '
                'created_at TIMESTAMP, '
                'updated_at TIMESTAMP, '
                'FOREIGN KEY(product_id) REFERENCES products (id), '
                'FOREIGN KEY(seller_id) REFERENCES users (id)'
                ')'
            )

        if 'upload_parts' not in table_names:
            alter_statements.append(
                'CREATE TABLE upload_parts ('
                f'id {id_column}, '
                'session_id INTEGER NOT NULL, '
                'file_id INTEGER NOT NULL, '
                'part_number INTEGER NOT NULL, '
                'etag VARCHAR(255), '
                'size_bytes BIGINT, '
                "status VARCHAR(30) DEFAULT 'pending', "
                'created_at TIMESTAMP, '
                'updated_at TIMESTAMP, '
                'FOREIGN KEY(session_id) REFERENCES upload_sessions (id), '
                'FOREIGN KEY(file_id) REFERENCES product_files (id), '
                'CONSTRAINT ux_upload_parts_file_part UNIQUE (file_id, part_number)'
                ')'
            )

        if 'product_images' not in table_names:
            alter_statements.append(
                'CREATE TABLE product_images ('
                f'id {id_column}, '
                'product_id INTEGER NOT NULL, '
                'image_filename VARCHAR(255) NOT NULL, '
                'sort_order INTEGER DEFAULT 0, '
                'created_at TIMESTAMP, '
                'FOREIGN KEY(product_id) REFERENCES products (id)'
                ')'
            )

        if 'upload_sessions' in table_names and 'upload_parts' in table_names and 'product_files' in table_names:
            # Best-effort FK column backfill for SQLite/MySQL-like environments.
            pass

        if 'product_ratings' not in table_names:
            alter_statements.append(
                'CREATE TABLE product_ratings ('
                f'id {id_column}, '
                'product_id INTEGER NOT NULL, '
                'user_id INTEGER NOT NULL, '
                'rating INTEGER NOT NULL, '
                'created_at TIMESTAMP, '
                'updated_at TIMESTAMP, '
                'FOREIGN KEY(product_id) REFERENCES products (id), '
                'FOREIGN KEY(user_id) REFERENCES users (id), '
                'CONSTRAINT ux_product_ratings_product_user UNIQUE (product_id, user_id)'
                ')'
            )

        if 'product_reactions' not in table_names:
            alter_statements.append(
                'CREATE TABLE product_reactions ('
                f'id {id_column}, '
                'product_id INTEGER NOT NULL, '
                'user_id INTEGER NOT NULL, '
                'reaction_type VARCHAR(20) NOT NULL, '
                'created_at TIMESTAMP, '
                'updated_at TIMESTAMP, '
                'FOREIGN KEY(product_id) REFERENCES products (id), '
                'FOREIGN KEY(user_id) REFERENCES users (id), '
                'CONSTRAINT ux_product_reactions_product_user UNIQUE (product_id, user_id)'
                ')'
            )

        if 'product_reviews' not in table_names:
            alter_statements.append(
                'CREATE TABLE product_reviews ('
                f'id {id_column}, '
                'product_id INTEGER NOT NULL, '
                'user_id INTEGER NOT NULL, '
                'title VARCHAR(140), '
                'content TEXT NOT NULL, '
                'created_at TIMESTAMP, '
                'updated_at TIMESTAMP, '
                'FOREIGN KEY(product_id) REFERENCES products (id), '
                'FOREIGN KEY(user_id) REFERENCES users (id), '
                'CONSTRAINT ux_product_reviews_product_user UNIQUE (product_id, user_id)'
                ')'
            )

        for statement in alter_statements:
            db.session.execute(text(statement))

        if alter_statements:
            db.session.commit()
