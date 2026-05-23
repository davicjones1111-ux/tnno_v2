"""
Migration Script
Migrate data from old SQLite database (system.db) to new SQLAlchemy models
"""
import sqlite3
import os
import sys
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import (
    User, Mission, UserMission, Post, PostInteraction,
    Deposit, WithdrawRequest, WorkRequest, ServiceOrder, GameScore
)


def migrate_data(old_db_path='system.db'):
    """Migrate all data from old database to new database"""
    app = create_app()
    
    if not os.path.exists(old_db_path):
        print(f"Error: Old database '{old_db_path}' not found!")
        return False
    
    print(f"Migrating data from {old_db_path}...")
    
    with app.app_context():
        db.create_all()
        
        old_conn = sqlite3.connect(old_db_path)
        old_conn.row_factory = sqlite3.Row
        old_cursor = old_conn.cursor()
        
        migrate_users(old_cursor)
        migrate_missions(old_cursor)
        migrate_user_missions(old_cursor)
        migrate_work_requests(old_cursor)
        migrate_service_orders(old_cursor)
        migrate_withdraw_requests(old_cursor)
        migrate_deposits(old_cursor)
        migrate_posts(old_cursor)
        migrate_post_interactions(old_cursor)
        
        old_conn.close()
        
        print("Migration completed successfully!")
        return True


def migrate_users(cursor):
    """Migrate users table"""
    print("Migrating users...")
    cursor.execute("SELECT * FROM users")
    users = cursor.fetchall()
    
    for row in users:
        existing = User.query.filter_by(username=row['username']).first()
        if existing:
            existing.coins = row.get('coins', 0)
            existing.bio = row.get('bio', '')
            existing.profile_pic = row.get('profile_pic', '')
            existing.user_6digit = row.get('user_6digit')
        else:
            user = User(
                username=row['username'],
                password_hash=row['password'],
                coins=row.get('coins', 0),
                user_6digit=row.get('user_6digit'),
                bio=row.get('bio', ''),
                profile_pic=row.get('profile_pic', ''),
                role='admin' if row['username'] == 'admin' else 'user'
            )
            db.session.add(user)
    
    db.session.commit()
    print(f"  Migrated {len(users)} users")


def migrate_missions(cursor):
    """Migrate missions table"""
    print("Migrating missions...")
    cursor.execute("SELECT * FROM missions")
    missions = cursor.fetchall()
    
    for row in missions:
        mission = Mission(
            id=row['id'],
            title=row['title'],
            instructions=row['instructions'],
            reward=row['reward'],
            limit_count=row.get('limit_count', 0),
            time_limit=row.get('time_limit', 24),
            status='active'
        )
        db.session.add(mission)
    
    db.session.commit()
    print(f"  Migrated {len(missions)} missions")


def migrate_user_missions(cursor):
    """Migrate user_missions table"""
    print("Migrating user missions...")
    cursor.execute("SELECT * FROM user_missions")
    user_missions = cursor.fetchall()
    
    for row in user_missions:
        submission_time = None
        mission_deadline = None
        
        if row.get('submission_time'):
            try:
                submission_time = datetime.fromisoformat(row['submission_time'])
            except:
                pass
        
        if row.get('mission_deadline'):
            try:
                mission_deadline = datetime.fromisoformat(row['mission_deadline'])
            except:
                pass
        
        user_mission = UserMission(
            id=row['id'],
            user_id=row['user_id'],
            mission_id=row['mission_id'],
            mission_title=row.get('mission_title'),
            code=row.get('code'),
            status=row.get('status', 'pending'),
            mission_photo=row.get('mission_photo'),
            submission_time=submission_time,
            mission_deadline=mission_deadline
        )
        db.session.add(user_mission)
    
    db.session.commit()
    print(f"  Migrated {len(user_missions)} user missions")


def migrate_work_requests(cursor):
    """Migrate work_requests table"""
    print("Migrating work requests...")
    cursor.execute("SELECT * FROM work_requests")
    work_requests = cursor.fetchall()
    
    for row in work_requests:
        work_req = WorkRequest(
            id=row['id'],
            user_id=row['user_id'],
            message=row['message'],
            file_path=row.get('file_path'),
            status=row.get('status', 'pending')
        )
        db.session.add(work_req)
    
    db.session.commit()
    print(f"  Migrated {len(work_requests)} work requests")


def migrate_service_orders(cursor):
    """Migrate service_orders table"""
    print("Migrating service orders...")
    cursor.execute("SELECT * FROM service_orders")
    orders = cursor.fetchall()
    
    for row in orders:
        order = ServiceOrder(
            id=row['id'],
            user_id=row['user_id'],
            category=row['category'],
            service=row['service'],
            link=row.get('link'),
            quantity=row.get('quantity', 1),
            charge=row['charge'],
            status=row.get('status', 'pending')
        )
        db.session.add(order)
    
    db.session.commit()
    print(f"  Migrated {len(orders)} service orders")


def migrate_withdraw_requests(cursor):
    """Migrate withdraw_requests table"""
    print("Migrating withdrawal requests...")
    cursor.execute("SELECT * FROM withdraw_requests")
    withdraws = cursor.fetchall()
    
    for row in withdraws:
        withdraw = WithdrawRequest(
            id=row['id'],
            user_id=row['user_id'],
            amount=row['amount'],
            wallet=row['wallet'],
            name=row['name'],
            status=row.get('status', 'pending')
        )
        db.session.add(withdraw)
    
    db.session.commit()
    print(f"  Migrated {len(withdraws)} withdrawal requests")


def migrate_deposits(cursor):
    """Migrate deposits table"""
    print("Migrating deposits...")
    cursor.execute("SELECT * FROM deposits")
    deposits = cursor.fetchall()
    
    for row in deposits:
        created_at = None
        if row.get('created_at'):
            try:
                created_at = datetime.fromisoformat(row['created_at'])
            except:
                pass
        
        deposit = Deposit(
            id=row['id'],
            user_id=row['user_id'],
            usdt_amount=row['usdt_amount'],
            points_amount=row.get('points_amount', 0),
            tx_hash=row.get('tx_hash'),
            status=row.get('status', 'pending'),
            blockchain_status=row.get('blockchain_status', 'unverified'),
            created_at=created_at,
            coins_added=row.get('coins_added')
        )
        db.session.add(deposit)
    
    db.session.commit()
    print(f"  Migrated {len(deposits)} deposits")


def migrate_posts(cursor):
    """Migrate posts table"""
    print("Migrating posts...")
    cursor.execute("SELECT * FROM posts")
    posts = cursor.fetchall()
    
    for row in posts:
        created_at = None
        if row.get('created_at'):
            try:
                created_at = datetime.fromisoformat(row['created_at'])
            except:
                pass
        
        post = Post(
            id=row['id'],
            user_id=row['user_id'],
            content=row['content'],
            image_path=row.get('image_path'),
            created_at=created_at
        )
        db.session.add(post)
    
    db.session.commit()
    print(f"  Migrated {len(posts)} posts")


def migrate_post_interactions(cursor):
    """Migrate post_interactions table"""
    print("Migrating post interactions...")
    cursor.execute("SELECT * FROM post_interactions")
    interactions = cursor.fetchall()
    
    for row in interactions:
        created_at = None
        if row.get('created_at'):
            try:
                created_at = datetime.fromisoformat(row['created_at'])
            except:
                pass
        
        interaction = PostInteraction(
            id=row['id'],
            post_id=row['post_id'],
            user_id=row['user_id'],
            interaction_type=row['interaction_type'],
            comment=row.get('comment'),
            created_at=created_at
        )
        db.session.add(interaction)
    
    db.session.commit()
    print(f"  Migrated {len(interactions)} post interactions")


if __name__ == '__main__':
    old_db = sys.argv[1] if len(sys.argv) > 1 else 'system.db'
    success = migrate_data(old_db)
    
    if success:
        print("\nMigration completed!")
    else:
        print("\nMigration failed!")
        sys.exit(1)
