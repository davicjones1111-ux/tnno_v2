"""
Database Models
SQLAlchemy ORM models for the RetroQuest Platform
"""
from datetime import datetime
from decimal import Decimal
from flask_login import UserMixin
from flask import current_app, has_app_context
from app.extensions import db, bcrypt
from app.datetime_utils import utc_now


class User(db.Model, UserMixin):
    """User model for authentication and profile"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    coins = db.Column(db.Float, default=0.0)
    user_6digit = db.Column(db.String(6), unique=True, nullable=True, index=True)
    bio = db.Column(db.Text, default='')
    profile_pic = db.Column(db.String(255), default='')
    seller_cover_photo = db.Column(db.String(255), default='')
    role = db.Column(db.String(20), default='user')  # user, admin
    is_seller = db.Column(db.Boolean, default=False)  # flag set by admin to allow selling
    seller_commission_rate = db.Column(db.Numeric(5, 4), default=0.03)  # platform fee rate (e.g., 0.03 for 3%)
    seller_expires_at = db.Column(db.DateTime, nullable=True)
    seller_reminder_sent_at = db.Column(db.DateTime, nullable=True)
    seller_sales_seen_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now, index=True)
    
    # Relationships
    missions = db.relationship('UserMission', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    posts = db.relationship('Post', backref='author', lazy='dynamic', cascade='all, delete-orphan')
    deposits = db.relationship('Deposit', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    withdraw_requests = db.relationship('WithdrawRequest', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    work_requests = db.relationship('WorkRequest', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    service_orders = db.relationship('ServiceOrder', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    game_scores = db.relationship('GameScore', backref='user', lazy='dynamic', cascade='all, delete-orphan')

    __table_args__ = (
        db.Index('ix_users_role_created_at', 'role', 'created_at'),
    )
    
    def set_password(self, password):
        """Hash and set user password"""
        rounds = 12
        if has_app_context():
            rounds = int(current_app.config.get('BCRYPT_LOG_ROUNDS', 12))
        self.password_hash = bcrypt.generate_password_hash(password, rounds).decode('utf-8')
    
    def check_password(self, password):
        """Verify user password"""
        if not self.password_hash or not self.password_hash.startswith(('$2a$', '$2b$', '$2y$')):
            if has_app_context():
                current_app.logger.warning('User %s has an invalid password hash format', self.id)
            return False
        return bcrypt.check_password_hash(self.password_hash, password)
    
    def is_admin(self):
        """Check if user is admin"""
        admin_username = 'admin'
        if has_app_context():
            admin_username = current_app.config.get('ADMIN_USER', 'admin')
        return self.role == 'admin' or self.username == admin_username

    @property
    def seller_active(self):
        """Return True if seller subscription is active."""
        if self.is_admin():
            return True
        if not self.is_seller:
            return False
        if not self.seller_expires_at:
            return False
        return self.seller_expires_at >= utc_now()

    @property
    def can_sell(self):
        """Return True if user has seller privileges (either flagged as seller or admin)."""
        return bool(self.seller_active or self.is_admin())
    
    def to_dict(self):
        """Convert user to dictionary"""
        return {
            'id': self.id,
            'username': self.username,
            'coins': self.coins,
            'user_6digit': self.user_6digit,
            'bio': self.bio,
            'profile_pic': self.profile_pic,
            'seller_cover_photo': self.seller_cover_photo,
            'role': self.role,
            'seller_expires_at': self.seller_expires_at.isoformat() if self.seller_expires_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
    
    def __repr__(self):
        return f'<User {self.username}>'


class SellerRequest(db.Model):
    """Seller access request with verification details."""
    __tablename__ = 'seller_requests'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    real_name = db.Column(db.String(120), nullable=False)
    country = db.Column(db.String(80), nullable=False)
    city = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    product_description = db.Column(db.Text, nullable=False)
    id_front_path = db.Column(db.String(255), nullable=False)
    id_back_path = db.Column(db.String(255), nullable=False)
    location_text = db.Column(db.String(255), nullable=True)
    location_lat = db.Column(db.Float, nullable=True)
    location_lng = db.Column(db.Float, nullable=True)
    plan_key = db.Column(db.String(10), nullable=False)
    plan_months = db.Column(db.Integer, nullable=False, default=1)
    plan_cost = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected
    created_at = db.Column(db.DateTime, default=utc_now)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by = db.Column(db.Integer, nullable=True)

    user = db.relationship('User', backref=db.backref('seller_requests', lazy='dynamic'))

    def __repr__(self):
        return f'<SellerRequest user={self.user_id} status={self.status}>'


class SellerRating(db.Model):
    """Rating given to a seller by a user."""
    __tablename__ = 'seller_ratings'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    rater_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    rating = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        db.UniqueConstraint('seller_id', 'rater_id', name='ux_seller_ratings_seller_rater'),
    )

    seller = db.relationship('User', foreign_keys=[seller_id], backref=db.backref('seller_ratings', lazy='dynamic'))
    rater = db.relationship('User', foreign_keys=[rater_id])

    def __repr__(self):
        return f'<SellerRating seller={self.seller_id} rater={self.rater_id} rating={self.rating}>'


class SellerReport(db.Model):
    """Report submitted about a seller."""
    __tablename__ = 'seller_reports'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    evidence_path = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), default='pending')  # pending, reviewed
    created_at = db.Column(db.DateTime, default=utc_now)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by = db.Column(db.Integer, nullable=True)

    seller = db.relationship('User', foreign_keys=[seller_id], backref=db.backref('seller_reports', lazy='dynamic'))
    reporter = db.relationship('User', foreign_keys=[reporter_id])

    def __repr__(self):
        return f'<SellerReport seller={self.seller_id} reporter={self.reporter_id} status={self.status}>'


class UserNotification(db.Model):
    """Notification sent to a user by admin."""
    __tablename__ = 'user_notifications'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    attachment_path = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    read_at = db.Column(db.DateTime, nullable=True)
    sent_by = db.Column(db.Integer, nullable=True)

    user = db.relationship('User', backref=db.backref('notifications', lazy='dynamic'))

    __table_args__ = (
        db.Index('ix_user_notifications_user_read_created', 'user_id', 'read_at', 'created_at'),
    )

    def __repr__(self):
        return f'<UserNotification user={self.user_id} read={self.read_at is not None}>'


class Mission(db.Model):
    """Mission model for tasks"""
    __tablename__ = 'missions'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False)
    instructions = db.Column(db.Text, nullable=False)
    reward = db.Column(db.Integer, nullable=False)
    limit_count = db.Column(db.Integer, default=0)
    time_limit = db.Column(db.Integer, default=24)  # hours
    status = db.Column(db.String(20), default='active')  # active, inactive
    mission_type = db.Column(db.String(50), default='default')  # default, social, work
    image_path = db.Column(db.String(255), nullable=True)  # Card image for visual missions
    created_at = db.Column(db.DateTime, default=utc_now)
    
    # Relationships
    submissions = db.relationship('UserMission', backref='mission', lazy='dynamic', cascade='all, delete-orphan')
    
    def to_dict(self):
        """Convert mission to dictionary"""
        return {
            'id': self.id,
            'title': self.title,
            'instructions': self.instructions,
            'reward': self.reward,
            'limit_count': self.limit_count,
            'time_limit': self.time_limit,
            'status': self.status,
            'mission_type': self.mission_type,
            'image_path': self.image_path
        }
    
    def __repr__(self):
        return f'<Mission {self.title}>'


class UserMission(db.Model):
    """User mission submission model"""
    __tablename__ = 'user_missions'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    mission_id = db.Column(db.Integer, db.ForeignKey('missions.id'), nullable=False, index=True)
    mission_title = db.Column(db.String(200), nullable=True)
    code = db.Column(db.String(50), nullable=True)
    status = db.Column(db.String(20), default='pending')  # pending, completed, rejected
    mission_photo = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now, index=True)
    submission_time = db.Column(db.DateTime, default=utc_now)
    mission_deadline = db.Column(db.DateTime, nullable=True)
    is_archived = db.Column(db.Boolean, default=False, index=True)
    
    def to_dict(self):
        """Convert user mission to dictionary"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'mission_id': self.mission_id,
            'mission_title': self.mission_title,
            'code': self.code,
            'status': self.status,
            'mission_photo': self.mission_photo,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'submission_time': self.submission_time.isoformat() if self.submission_time else None,
            'mission_deadline': self.mission_deadline.isoformat() if self.mission_deadline else None,
            'is_archived': bool(self.is_archived)
        }
    
    def __repr__(self):
        return f'<UserMission user={self.user_id} mission={self.mission_id} status={self.status}>'


class Post(db.Model):
    """Social feed post model - 4chan style anonymous posts"""
    __tablename__ = 'posts'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=True, index=True)  # For threaded replies
    content = db.Column(db.Text, nullable=False)
    image_path = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now, index=True)
    post_number = db.Column(db.String(20), nullable=True, index=True)  # Unique post number like 1234567

    # Relationships
    interactions = db.relationship('PostInteraction', backref='post', lazy='dynamic', cascade='all, delete-orphan')
    replies = db.relationship('Post', backref=db.backref('parent', remote_side=[id]), lazy='select')

    __table_args__ = (
        db.Index('ix_posts_parent_created_at', 'parent_id', 'created_at'),
        db.Index('ix_posts_user_created_at', 'user_id', 'created_at'),
    )

    def to_dict(self):
        """Convert post to dictionary"""
        # `replies` is configured with lazy='select', so it's list-like in memory.
        replies_count = len(self.replies) if self.replies is not None else 0
        return {
            'id': self.id,
            'post_number': self.post_number,
            'user_id': self.user_id,
            'author': self.author.username if self.author else None,
            'content': self.content,
            'image_path': self.image_path,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'parent_id': self.parent_id,
            'replies_count': replies_count,
            'likes_count': self.interactions.filter_by(interaction_type='like').count(),
            'comments_count': self.interactions.filter_by(interaction_type='comment').count()
        }

    def __repr__(self):
        return f'<Post {self.id} by {self.user_id}>'


class PostInteraction(db.Model):
    """Post interaction model (likes, comments)"""
    __tablename__ = 'post_interactions'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    interaction_type = db.Column(db.String(20), nullable=False)  # like, comment
    comment = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    
    # Relationships
    user = db.relationship(
        'User',
        backref=db.backref('interactions', cascade='all, delete-orphan')
    )
    
    def __repr__(self):
        return f'<PostInteraction {self.interaction_type} by {self.user_id} on {self.post_id}>'


class Deposit(db.Model):
    """Cryptocurrency deposit model"""
    __tablename__ = 'deposits'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)  # Amount in USDT for a deposit
    network = db.Column(db.String(20), nullable=False)  # TRC20, ERC20, BEP20
    payment_id = db.Column(db.String(255), unique=True, nullable=False, index=True)
    coin_type = db.Column(db.String(20), default='USDT', nullable=False)  # Legacy support: USDT, BNB, BUSD, USDC
    usdt_amount = db.Column(db.Float, nullable=True)  # Legacy field for older deposit records
    expected_amount = db.Column(db.Numeric(24, 6), nullable=True, index=True)  # Legacy unique amount to pay
    points_amount = db.Column(db.Integer, nullable=True)  # Legacy coins to credit
    tx_hash = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(20), default='pending', index=True)  # pending, completed, expired
    is_archived = db.Column(db.Boolean, default=False, index=True)
    blockchain_status = db.Column(db.String(20), default='unverified')  # Legacy blockchain status
    created_at = db.Column(db.DateTime, default=utc_now, index=True)
    expires_at = db.Column(db.DateTime, nullable=True, index=True)
    verified_at = db.Column(db.DateTime, nullable=True)
    paid_at = db.Column(db.DateTime, nullable=True)
    credited_at = db.Column(db.DateTime, nullable=True)
    coins_added = db.Column(db.Integer, nullable=True)
    confirmations = db.Column(db.Integer, nullable=True, default=0)
    tx_block_number = db.Column(db.BigInteger, nullable=True)
    scan_from_block = db.Column(db.BigInteger, nullable=True)
    last_scanned_block = db.Column(db.BigInteger, nullable=True)
    last_check = db.Column(db.DateTime, nullable=True)
    
    def to_dict(self):
        """Convert deposit to dictionary"""
        expected_amount = self.expected_amount
        if isinstance(expected_amount, Decimal):
            expected_amount = float(expected_amount)

        return {
            'id': self.id,
            'user_id': self.user_id,
            'amount': self.amount,
            'network': self.network,
            'payment_id': self.payment_id,
            'coin_type': self.coin_type,
            'usdt_amount': self.usdt_amount,
            'expected_amount': expected_amount,
            'points_amount': self.points_amount,
            'tx_hash': self.tx_hash,
            'status': self.status,
            'is_archived': bool(self.is_archived),
            'blockchain_status': self.blockchain_status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'confirmations': self.confirmations,
            'coins_added': self.coins_added
        }
    
    def __repr__(self):
        return f'<Deposit {self.id} user={self.user_id} coin={self.coin_type} amount={self.usdt_amount}>'


class BlockchainState(db.Model):
    """Track last scanned block per coin type for efficient scanning"""
    __tablename__ = 'blockchain_state'

    coin_type = db.Column(db.String(20), primary_key=True)
    last_block = db.Column(db.BigInteger, nullable=False, default=0)

    def __repr__(self):
        return f'<BlockchainState {self.coin_type} last={self.last_block}>'

    @staticmethod
    def get_or_create(coin_type: str):
        state = BlockchainState.query.get(coin_type)
        if not state:
            state = BlockchainState(coin_type=coin_type, last_block=0)
            db.session.add(state)
            db.session.commit()
        return state

    @staticmethod
    def update_block(coin_type: str, block: int):
        state = BlockchainState.get_or_create(coin_type)
        state.last_block = block
        db.session.commit()


class WithdrawRequest(db.Model):
    """Withdrawal request model"""
    __tablename__ = 'withdraw_requests'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    amount = db.Column(db.Integer, nullable=False)
    wallet = db.Column(db.String(100), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected
    created_at = db.Column(db.DateTime, default=utc_now)
    is_archived = db.Column(db.Boolean, default=False, index=True)

    __table_args__ = (
        db.Index('ix_withdraw_requests_user_status_created', 'user_id', 'status', 'created_at'),
    )
    
    def to_dict(self):
        """Convert withdraw request to dictionary"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'amount': self.amount,
            'wallet': self.wallet,
            'name': self.name,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'is_archived': bool(self.is_archived)
        }
    
    def __repr__(self):
        return f'<WithdrawRequest {self.id} user={self.user_id} amount={self.amount}>'


class WorkRequest(db.Model):
    """Work request model"""
    __tablename__ = 'work_requests'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    file_path = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), default='pending')  # pending, accepted, rejected
    created_at = db.Column(db.DateTime, default=utc_now)
    is_archived = db.Column(db.Boolean, default=False, index=True)
    
    def to_dict(self):
        """Convert work request to dictionary"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'message': self.message,
            'file_path': self.file_path,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'is_archived': bool(self.is_archived)
        }
    
    def __repr__(self):
        return f'<WorkRequest {self.id} user={self.user_id}>'


class ServiceOrder(db.Model):
    """Service order model"""
    __tablename__ = 'service_orders'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    category = db.Column(db.String(50), nullable=False)
    service = db.Column(db.String(100), nullable=False)
    link = db.Column(db.String(255), nullable=True)
    quantity = db.Column(db.Integer, default=1)
    charge = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, completed, rejected
    created_at = db.Column(db.DateTime, default=utc_now)
    is_archived = db.Column(db.Boolean, default=False, index=True)
    
    def to_dict(self):
        """Convert service order to dictionary"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'category': self.category,
            'service': self.service,
            'link': self.link,
            'quantity': self.quantity,
            'charge': self.charge,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'is_archived': bool(self.is_archived)
        }
    
    def __repr__(self):
        return f'<ServiceOrder {self.id} user={self.user_id}>'


class HistoryEntry(db.Model):
    """Unified history log entry used by user/admin history screens."""
    __tablename__ = 'history_entries'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    source_key = db.Column(db.String(40), nullable=False, index=True)
    source_id = db.Column(db.Integer, nullable=False, index=True)
    type = db.Column(db.String(30), nullable=False, index=True)
    section = db.Column(db.String(80), nullable=True)
    status = db.Column(db.String(30), nullable=False, default='pending', index=True)
    is_archived = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(db.DateTime, default=utc_now, index=True)
    summary = db.Column(db.Text, nullable=True)
    link = db.Column(db.String(255), nullable=True)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    user = db.relationship(
        'User',
        backref=db.backref(
            'history_entries',
            lazy='dynamic',
            cascade='all, delete-orphan'
        )
    )

    __table_args__ = (
        db.UniqueConstraint(
            'user_id',
            'source_key',
            'source_id',
            'type',
            name='ux_history_entries_user_source'
        ),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'source_key': self.source_key,
            'source_id': self.source_id,
            'type': self.type,
            'section': self.section,
            'status': self.status,
            'is_archived': bool(self.is_archived),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'summary': self.summary,
            'link': self.link,
        }

    def __repr__(self):
        return f'<HistoryEntry user={self.user_id} type={self.type} source={self.source_key}:{self.source_id}>'


class GameScore(db.Model):
    """Game score model for leaderboard"""
    __tablename__ = 'game_scores'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    score = db.Column(db.Integer, default=0)
    game_id = db.Column(db.String(50), default='emperors_circle')
    created_at = db.Column(db.DateTime, default=utc_now)
    
    def to_dict(self):
        """Convert game score to dictionary"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'username': self.user.username if self.user else None,
            'score': self.score,
            'game_id': self.game_id,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
    
    def __repr__(self):
        return f'<GameScore user={self.user_id} score={self.score}>'


class EmperorMatchStat(db.Model):
    """Aggregated PvP stats for Emperor's Circle leaderboard."""
    __tablename__ = 'emperor_match_stats'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True, index=True)
    matches_played = db.Column(db.Integer, default=0)
    matches_won = db.Column(db.Integer, default=0)
    total_winnings = db.Column(db.Integer, default=0)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    user = db.relationship(
        'User',
        backref=db.backref(
            'emperor_match_stat',
            uselist=False,
            cascade='all, delete-orphan'
        ),
        single_parent=True
    )

    def __repr__(self):
        return f'<EmperorMatchStat user={self.user_id} played={self.matches_played} won={self.matches_won}>'


# ==================== MERCH STORE MODELS ====================

class Product(db.Model):
    """Product for sale in merch store (digital or physical)."""
    __tablename__ = 'products'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    price = db.Column(db.Integer, nullable=False)  # Price in coins
    image_filename = db.Column(db.String(255), nullable=True)  # Thumbnail image
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    product_type = db.Column(db.String(20), default='digital', index=True)  # digital, physical
    contact_link = db.Column(db.String(255), nullable=True)  # For physical products
    physical_quantity = db.Column(db.Integer, default=0)  # For physical products
    created_at = db.Column(db.DateTime, default=utc_now)
    is_active = db.Column(db.Boolean, default=True)
    
    # Relationships
    seller = db.relationship('User', backref=db.backref('products', lazy='dynamic'))
    files = db.relationship('ProductFile', backref='product', lazy='dynamic', cascade='all, delete-orphan')
    images = db.relationship(
        'ProductImage',
        backref='product',
        lazy='dynamic',
        cascade='all, delete-orphan',
        order_by='ProductImage.sort_order.asc()'
    )
    orders = db.relationship('MerchOrder', backref='product', lazy='dynamic')
    ratings = db.relationship('ProductRating', backref='product', lazy='dynamic', cascade='all, delete-orphan')
    reactions = db.relationship('ProductReaction', backref='product', lazy='dynamic', cascade='all, delete-orphan')
    reviews = db.relationship(
        'ProductReview',
        backref='product',
        lazy='dynamic',
        cascade='all, delete-orphan',
        order_by='ProductReview.updated_at.desc()'
    )

    __table_args__ = (
        db.Index('ix_products_active_created_type', 'is_active', 'created_at', 'product_type'),
        db.Index('ix_products_seller_active_created', 'seller_id', 'is_active', 'created_at'),
    )
    
    @property
    def quantity(self):
        """Get available quantity (unsold files)"""
        if self.product_type == 'physical':
            return max(int(self.physical_quantity or 0), 0)
        return ProductFile.query.filter_by(product_id=self.id, is_sold=False).count()
    
    @property
    def total_files(self):
        """Get total files uploaded"""
        if self.product_type == 'physical':
            return max(int(self.physical_quantity or 0), 0)
        return ProductFile.query.filter_by(product_id=self.id).count()

    @property
    def gallery_filenames(self):
        """Return up to four product image filenames including the cover image."""
        filenames = []
        if self.image_filename:
            filenames.append(self.image_filename)
        for image in self.images.all():
            if image.image_filename and image.image_filename not in filenames:
                filenames.append(image.image_filename)
        return filenames[:4]
    
    def __repr__(self):
        return f'<Product {self.name} seller={self.seller_id}>'


class ProductImage(db.Model):
    """Gallery image for a merch product."""
    __tablename__ = 'product_images'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False, index=True)
    image_filename = db.Column(db.String(255), nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=utc_now)

    def __repr__(self):
        return f'<ProductImage product={self.product_id} order={self.sort_order}>'


class ProductRating(db.Model):
    """Per-user product star rating."""
    __tablename__ = 'product_ratings'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    rating = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    user = db.relationship('User', backref=db.backref('product_ratings', lazy='dynamic'))

    __table_args__ = (
        db.UniqueConstraint('product_id', 'user_id', name='ux_product_ratings_product_user'),
    )

    def __repr__(self):
        return f'<ProductRating product={self.product_id} user={self.user_id} rating={self.rating}>'


class ProductReaction(db.Model):
    """Simple per-user like or dislike on a product."""
    __tablename__ = 'product_reactions'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    reaction_type = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    user = db.relationship('User', backref=db.backref('product_reactions', lazy='dynamic'))

    __table_args__ = (
        db.UniqueConstraint('product_id', 'user_id', name='ux_product_reactions_product_user'),
    )

    def __repr__(self):
        return f'<ProductReaction product={self.product_id} user={self.user_id} type={self.reaction_type}>'


class ProductReview(db.Model):
    """Per-user written review for a product."""
    __tablename__ = 'product_reviews'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    title = db.Column(db.String(140), nullable=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    user = db.relationship('User', backref=db.backref('product_reviews', lazy='dynamic'))

    __table_args__ = (
        db.UniqueConstraint('product_id', 'user_id', name='ux_product_reviews_product_user'),
    )

    def __repr__(self):
        return f'<ProductReview product={self.product_id} user={self.user_id}>'


class ProductFile(db.Model):
    """Individual file for each product quantity"""
    __tablename__ = 'product_files'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False, index=True)
    file_filename = db.Column(db.String(255), nullable=False)  # Stored filename
    original_name = db.Column(db.String(255), nullable=True)  # Original filename for display
    is_sold = db.Column(db.Boolean, default=False)
    order_id = db.Column(db.Integer, db.ForeignKey('merch_orders.id'), nullable=True)
    sold_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    
    def __repr__(self):
        return f'<ProductFile product={self.product_id} sold={self.is_sold}>'


class MerchOrder(db.Model):
    """Order for merch products"""
    __tablename__ = 'merch_orders'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False, index=True)
    product_type = db.Column(db.String(20), default='digital')  # digital, physical snapshot
    quantity = db.Column(db.Integer, nullable=False)
    total_price = db.Column(db.Integer, nullable=False)  # Price at time of purchase
    status = db.Column(db.String(20), default='completed')  # completed, pending, delivered, refunded
    created_at = db.Column(db.DateTime, default=utc_now, index=True)
    purchased_at = db.Column(db.DateTime, default=utc_now)
    shipping_name = db.Column(db.String(120), nullable=True)
    shipping_country = db.Column(db.String(120), nullable=True)
    shipping_city = db.Column(db.String(120), nullable=True)
    shipping_phone = db.Column(db.String(40), nullable=True)
    shipping_lat = db.Column(db.Float, nullable=True)
    shipping_lng = db.Column(db.Float, nullable=True)
    shipping_location_text = db.Column(db.String(255), nullable=True)
    delivery_eta = db.Column(db.DateTime, nullable=True)
    delivered_at = db.Column(db.DateTime, nullable=True)
    refunded_at = db.Column(db.DateTime, nullable=True)
    is_archived = db.Column(db.Boolean, default=False, index=True)
    
    # Relationship to files
    files = db.relationship('ProductFile', backref='order', lazy='dynamic')
    
    user = db.relationship(
        'User',
        backref=db.backref(
            'merch_orders',
            lazy='dynamic',
            cascade='all, delete-orphan'
        )
    )

    __table_args__ = (
        db.Index('ix_merch_orders_user_created_at', 'user_id', 'created_at'),
        db.Index('ix_merch_orders_product_created_at', 'product_id', 'created_at'),
    )
    
    def __repr__(self):
        return f'<MerchOrder user={self.user_id} product={self.product_id} qty={self.quantity}>'


class SellerChatConversation(db.Model):
    """Conversation between buyer and seller"""
    __tablename__ = 'seller_chat_conversations'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    buyer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)
    
    buyer = db.relationship('User', foreign_keys=[buyer_id], backref=db.backref('buyer_conversations', lazy='dynamic'))
    seller = db.relationship('User', foreign_keys=[seller_id], backref=db.backref('seller_conversations', lazy='dynamic'))
    product = db.relationship('Product', backref=db.backref('conversations', lazy='dynamic'))
    messages = db.relationship('SellerChatMessage', backref='conversation', lazy='dynamic', cascade='all, delete-orphan')

    __table_args__ = (
        db.Index('ix_seller_chat_buyer_updated_at', 'buyer_id', 'updated_at'),
        db.Index('ix_seller_chat_seller_updated_at', 'seller_id', 'updated_at'),
    )
    
    @property
    def last_message(self):
        return SellerChatMessage.query.filter_by(conversation_id=self.id).order_by(SellerChatMessage.created_at.desc()).first()
    
    def unread_count(self, user_id=None):
        if not user_id:
            return 0
        return SellerChatMessage.query.filter(
            SellerChatMessage.conversation_id == self.id,
            SellerChatMessage.sender_id != user_id,
            SellerChatMessage.is_read.is_(False)
        ).count()
    
    def __repr__(self):
        return f'<SellerChatConversation buyer={self.buyer_id} seller={self.seller_id}>'


class SellerChatMessage(db.Model):
    """Message in a seller chat conversation"""
    __tablename__ = 'seller_chat_messages'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('seller_chat_conversations.id'), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    message_type = db.Column(db.String(20), default='text')  # text, image
    content = db.Column(db.Text, nullable=True)  # For text messages
    image_path = db.Column(db.String(255), nullable=True)  # For image messages
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utc_now)
    
    sender = db.relationship('User', backref=db.backref('chat_messages', lazy='dynamic'))

    __table_args__ = (
        db.Index('ix_seller_chat_messages_conversation_created', 'conversation_id', 'created_at'),
        db.Index('ix_seller_chat_messages_conversation_read', 'conversation_id', 'is_read'),
    )
    
    def __repr__(self):
        return f'<SellerChatMessage conversation={self.conversation_id} sender={self.sender_id}>'


class SellerNotification(db.Model):
    """Notification for sellers (messages and purchases)"""
    __tablename__ = 'seller_notifications'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    notification_type = db.Column(db.String(30), nullable=False)  # new_message, new_purchase
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=True)
    related_id = db.Column(db.Integer, nullable=True)  # conversation_id or order_id
    related_type = db.Column(db.String(30), nullable=True)  # conversation, order
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utc_now)
    
    seller = db.relationship('User', backref=db.backref('seller_notifications', lazy='dynamic'))

    __table_args__ = (
        db.Index('ix_seller_notifications_seller_read_created', 'seller_id', 'is_read', 'created_at'),
    )

    def __repr__(self):
        return f'<SellerNotification seller={self.seller_id} type={self.notification_type}>'


class WalletTransaction(db.Model):
    """Immutable wallet ledger entry for balance-affecting events."""
    __tablename__ = 'wallet_transactions'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    transaction_type = db.Column(db.String(40), nullable=False, index=True)
    amount = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='completed', index=True)
    balance_before = db.Column(db.Float, nullable=True)
    balance_after = db.Column(db.Float, nullable=True)
    reference_type = db.Column(db.String(40), nullable=True, index=True)
    reference_id = db.Column(db.Integer, nullable=True, index=True)
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now, index=True)

    user = db.relationship(
        'User',
        backref=db.backref('wallet_transactions', lazy='dynamic', cascade='all, delete-orphan')
    )

    __table_args__ = (
        db.Index('ix_wallet_transactions_user_created', 'user_id', 'created_at'),
    )

    def __repr__(self):
        return f'<WalletTransaction user={self.user_id} type={self.transaction_type} amount={self.amount}>'
