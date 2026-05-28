"""
User Service
Business logic for user management
"""
from __future__ import annotations

import re

from flask import current_app
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.extensions import bcrypt, db
from app.models import User, GameScore, PasswordHistory
from app.utils import generate_unique_6digit_id
from app.validators import ValidationError, validate_email, validate_password, validate_username


def _username_similarity_key(value: str) -> str:
    return re.sub(r'[._]+', '', (value or '').strip().lower())


def _levenshtein_distance_at_most_one(left: str, right: str) -> bool:
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False

    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    index_short = 0
    index_long = 0
    edits = 0

    while index_short < len(shorter) and index_long < len(longer):
        if shorter[index_short] == longer[index_long]:
            index_short += 1
            index_long += 1
            continue

        edits += 1
        if edits > 1:
            return False

        if len(shorter) == len(longer):
            index_short += 1
            index_long += 1
        else:
            index_long += 1

    edits += (len(shorter) - index_short) + (len(longer) - index_long)
    return edits <= 1


class UserService:
    """Service for managing users"""

    @staticmethod
    def is_username_too_similar(candidate_username: str, exclude_user_id: int | None = None):
        """Return True if a candidate is dangerously close to an existing username."""
        candidate = (candidate_username or '').strip().lower()
        if not candidate:
            return False, None

        candidate_key = _username_similarity_key(candidate)
        query = User.query.with_entities(User.id, User.username)
        if exclude_user_id is not None:
            query = query.filter(User.id != exclude_user_id)

        for existing_id, existing_username in query.yield_per(200):
            existing_value = (existing_username or '').strip().lower()
            if not existing_value:
                continue
            if candidate == existing_value:
                return True, existing_value
            existing_key = _username_similarity_key(existing_value)
            if candidate_key and existing_key and _levenshtein_distance_at_most_one(candidate_key, existing_key):
                return True, existing_value

        return False, None
    
    @staticmethod
    def create_user(username, password, email=None):
        """Create a new user"""
        try:
            username = validate_username(username)
            password = validate_password(password)
            email = validate_email(email)
        except ValidationError as exc:
            return None, str(exc)

        admin_username = current_app.config.get('ADMIN_USER', 'admin')
        if username.lower() in {admin_username.lower(), 'admin'}:
            return None, "This username is reserved"

        existing = User.query.filter(func.lower(User.username) == username.lower()).first()
        if existing:
            return None, "Your username is already taken"

        too_similar, similar_to = UserService.is_username_too_similar(username)
        if too_similar:
            return None, f"Your username is too similar to '{similar_to}'"

        if email:
            existing = User.query.filter(func.lower(User.email) == email.lower()).first()
            if existing:
                return None, "Email already exists"

        user = User(
            username=username,
            email=email,
            user_6digit=generate_unique_6digit_id()
        )
        user.set_password(password)

        try:
            db.session.add(user)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return None, "Unable to create account right now. Please try again."

        UserService.record_password_history(user)
        db.session.commit()

        return user, "User created successfully"
    
    @staticmethod
    def authenticate_user(username, password):
        """Authenticate user with username/email and password."""
        identifier = (username or '').strip()
        if not identifier:
            return None, "Invalid username or email or password"

        user = UserService.get_user_by_identifier(identifier)
        if not user:
            return None, "Invalid username or password"
        
        if not user.check_password(password):
            return None, "Invalid username or password"
        
        return user, "Authentication successful"

    @staticmethod
    def get_user_by_identifier(identifier):
        """Get a user by username or email."""
        identifier = (identifier or '').strip()
        if not identifier:
            return None
        lowered = identifier.lower()
        if '@' in lowered:
            user = User.query.filter(func.lower(User.email) == lowered).first()
            if user:
                return user
        return User.query.filter(func.lower(User.username) == lowered).first()
    
    @staticmethod
    def get_user_by_id(user_id):
        """Get user by ID"""
        return User.query.get(user_id)
    
    @staticmethod
    def get_user_by_username(username):
        """Get user by username"""
        username = (username or '').strip()
        if not username:
            return None
        return User.query.filter(func.lower(User.username) == username.lower()).first()
    
    @staticmethod
    def get_user_by_6digit(user_6digit):
        """Get user by 6-digit ID"""
        return User.query.filter_by(user_6digit=user_6digit).first()
    
    @staticmethod
    def update_user_profile(user_id, **kwargs):
        """Update user profile"""
        user = User.query.get(user_id)
        if not user:
            return None, "User not found"
        
        allowed_fields = ['bio', 'profile_pic', 'email']
        for key, value in kwargs.items():
            if key in allowed_fields and hasattr(user, key):
                setattr(user, key, value)
        
        db.session.commit()
        return user, "Profile updated"

    @staticmethod
    def record_password_history(user):
        if not user or not user.id or not user.password_hash:
            return
        existing = PasswordHistory.query.filter_by(user_id=user.id, password_hash=user.password_hash).first()
        if existing:
            return
        db.session.add(PasswordHistory(user_id=user.id, password_hash=user.password_hash))

    @staticmethod
    def is_password_reused(user, candidate_password):
        if not user or not user.id:
            return False
        history_count = int(current_app.config.get('PASSWORD_HISTORY_COUNT', 5))
        recent_hashes = PasswordHistory.query.filter_by(user_id=user.id)\
            .order_by(PasswordHistory.created_at.desc())\
            .limit(history_count)\
            .all()
        return any(
            hash_row.password_hash and hash_row.password_hash.startswith(('$2a$', '$2b$', '$2y$'))
            and bcrypt.check_password_hash(hash_row.password_hash, candidate_password)
            for hash_row in recent_hashes
        )
    
    @staticmethod
    def update_user_coins(user_id, amount):
        """Add or subtract TNNO from user"""
        user = User.query.get(user_id)
        if not user:
            return False
        
        user.coins += amount
        if user.coins < 0:
            user.coins = 0
        
        db.session.commit()
        return True
    
    @staticmethod
    def get_all_users(limit=100):
        """Get all users with limit"""
        return User.query.order_by(User.created_at.desc()).limit(limit).all()
    
    @staticmethod
    def search_users(query, limit=20):
        """Search users by username"""
        return User.query.filter(
            User.username.ilike(f'%{query}%')
        ).limit(limit).all()
    
    @staticmethod
    def get_leaderboard(limit=10):
        """Get users ranked by TNNO"""
        admin_username = (current_app.config.get('ADMIN_USER', 'admin') or 'admin').lower()
        return User.query.filter(
            User.role != 'admin',
            func.lower(User.username) != admin_username
        ).order_by(User.coins.desc()).limit(limit).all()
    
    @staticmethod
    def save_game_score(user_id, score, game_id='emperors_circle'):
        """Save user's game score"""
        # Get or create game score
        game_score = GameScore.query.filter_by(
            user_id=user_id,
            game_id=game_id
        ).first()
        
        if not game_score:
            game_score = GameScore(
                user_id=user_id,
                score=score,
                game_id=game_id
            )
            db.session.add(game_score)
        else:
            # Update if new score is higher
            if score > game_score.score:
                game_score.score = score
        
        db.session.commit()
        return game_score
    
    @staticmethod
    def get_game_leaderboard(game_id='emperors_circle', limit=10):
        """Get game leaderboard"""
        return GameScore.query.filter_by(game_id=game_id)\
            .order_by(GameScore.score.desc())\
            .limit(limit).all()
    
    @staticmethod
    def get_user_stats(user_id):
        """Get comprehensive user statistics"""
        from app.models import UserMission, Post, Deposit, WithdrawRequest
        
        user = User.query.get(user_id)
        if not user:
            return None
        
        # Count completed missions
        completed_missions = UserMission.query.filter_by(
            user_id=user_id, 
            status='completed'
        ).count()
        
        # Count posts
        total_posts = Post.query.filter_by(user_id=user_id).count()
        
        # Count deposits
        total_deposits = Deposit.query.filter(
            Deposit.user_id == user_id,
            Deposit.status.in_(['success', 'completed'])
        ).count()
        
        # Count withdrawals
        total_withdraws = WithdrawRequest.query.filter_by(
            user_id=user_id, 
            status='approved'
        ).count()
        
        # Get best game score
        best_score = GameScore.query.filter_by(
            user_id=user_id,
            game_id='emperors_circle'
        ).first()
        
        return {
            'user': user,
            'completed_missions': completed_missions,
            'total_posts': total_posts,
            'total_deposits': total_deposits,
            'total_withdraws': total_withdraws,
            'best_game_score': best_score.score if best_score else 0
        }
