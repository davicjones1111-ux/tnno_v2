"""
Mission Service
Business logic for mission management
"""
from datetime import datetime, timedelta
from flask import current_app
from sqlalchemy import inspect, text
from app.datetime_utils import utc_now
from app.extensions import db, cache
from app.models import Mission, UserMission, User
from app.services.history_service import HistoryService


class MissionService:
    """Service for managing missions and submissions"""

    @staticmethod
    def ensure_mission_schema():
        """Best-effort schema patching for mission-related fields."""
        inspector = inspect(db.engine)
        if 'missions' not in inspector.get_table_names():
            return

        mission_cols = {col['name'] for col in inspector.get_columns('missions')}
        alter_statements = []

        if 'image_path' not in mission_cols:
            alter_statements.append('ALTER TABLE missions ADD COLUMN image_path VARCHAR(255)')

        for statement in alter_statements:
            db.session.execute(text(statement))

        if alter_statements:
            db.session.commit()
    
    @staticmethod
    @cache.cached(timeout=60, key_prefix='active_missions')
    def get_active_missions():
        """Get all active missions.

        A mission is considered active when its status is 'active'.  The
        `approve_submission` logic will automatically flip a mission to
        'inactive' once its `limit_count` reaches zero, ensuring that the
        board never displays a mission that has already been claimed the
        configured number of times.
        
        Cached for 60 seconds for performance.
        """
        return (Mission.query
                .filter_by(status='active')
                .order_by(Mission.created_at.desc())
                .all())
    
    @staticmethod
    @cache.memoize(timeout=120)
    def get_mission_by_id(mission_id):
        """Get mission by ID - cached for 2 minutes"""
        return Mission.query.get(mission_id)
    
    @staticmethod
    def create_mission(title, instructions, reward, limit_count=0, time_limit=24, mission_type='default', image_path=None):
        """Create a new mission"""
        mission = Mission(
            title=title,
            instructions=instructions,
            reward=reward,
            limit_count=limit_count,
            time_limit=time_limit,
            mission_type=mission_type,
            image_path=image_path,
            status='active'
        )
        db.session.add(mission)
        db.session.commit()
        # Clear cache when new mission is created
        cache.delete('active_missions')
        return mission
    
    @staticmethod
    def update_mission(mission_id, **kwargs):
        """Update mission details"""
        mission = Mission.query.get(mission_id)
        if not mission:
            return None
        
        for key, value in kwargs.items():
            if hasattr(mission, key):
                setattr(mission, key, value)
        
        db.session.commit()
        cache.delete('active_missions')
        cache.delete_memoized(MissionService.get_mission_by_id, mission_id)
        return mission
    
    @staticmethod
    def delete_mission(mission_id):
        """Delete a mission permanently"""
        mission = Mission.query.get(mission_id)
        if not mission:
            return False

        db.session.delete(mission)
        db.session.commit()
        cache.delete('active_missions')
        cache.delete_memoized(MissionService.get_mission_by_id, mission_id)
        return True
    
    @staticmethod
    def submit_mission(user_id, mission_id, code=None, photo_path=None):
        """Submit mission for approval"""
        mission = Mission.query.get(mission_id)
        if not mission or mission.status != 'active':
            return None, "Mission not found or inactive"

        existing_submissions = UserMission.query.filter_by(
            user_id=user_id,
            mission_id=mission_id
        ).all()

        # Prevent duplicate in-progress submissions.
        if any(sub.status == 'pending' for sub in existing_submissions):
            return None, "You already have a pending submission for this mission"
        
        # Create submission
        submission = UserMission(
            user_id=user_id,
            mission_id=mission_id,
            mission_title=mission.title,
            code=code,
            mission_photo=photo_path,
            status='pending',
            mission_deadline=utc_now() + timedelta(hours=mission.time_limit)
        )
        
        db.session.add(submission)
        db.session.commit()
        
        return submission, "Mission submitted successfully"
    
    @staticmethod
    def get_user_submissions(user_id, status=None, page=None, per_page=20, include_archived=False):
        """Get user's mission submissions."""
        query = UserMission.query.filter_by(user_id=user_id)
        if not include_archived:
            query = query.filter(UserMission.is_archived.is_(False))
        if status:
            query = query.filter_by(status=status)
        query = query.order_by(UserMission.submission_time.desc())
        if page is not None:
            return query.paginate(page=page, per_page=per_page, error_out=False)
        return query.all()
    
    @staticmethod
    def get_pending_submissions():
        """Get all pending submissions"""
        return UserMission.query.filter_by(status='pending')\
            .order_by(UserMission.submission_time.asc()).all()
    
    @staticmethod
    def approve_submission(submission_id, admin_id):
        """Approve mission submission and reward user"""
        submission = UserMission.query.get(submission_id)
        if not submission:
            return False, "Submission not found"
        
        if submission.status != 'pending':
            return False, "Submission already processed"
        
        # Get mission reward
        mission = Mission.query.get(submission.mission_id)
        if not mission:
            return False, "Mission not found"
        
        # Update user TNNO
        user = User.query.get(submission.user_id)
        if user:
            user.coins += mission.reward
        
        # decrement mission usage limit if applicable
        if mission.limit_count and mission.limit_count > 0:
            mission.limit_count -= 1
            # if we just reached zero, deactivate the mission so it disappears from the board
            if mission.limit_count <= 0:
                mission.status = 'inactive'
        
        # Update submission status
        submission.status = 'completed'
        HistoryService.mark_archived_if_terminal(submission, 'missions')
        db.session.commit()
        cache.delete('active_missions')
        cache.delete_memoized(MissionService.get_mission_by_id, mission.id)
        
        return True, f"Mission approved. {mission.reward} TNNO awarded."
    
    @staticmethod
    def reject_submission(submission_id, reason=None):
        """Reject mission submission"""
        submission = UserMission.query.get(submission_id)
        if not submission:
            return False, "Submission not found"
        
        submission.status = 'rejected'
        HistoryService.mark_archived_if_terminal(submission, 'missions')
        db.session.commit()
        
        return True, "Mission rejected"
    
    @staticmethod
    def get_mission_stats(mission_id):
        """Get mission submission statistics"""
        mission = Mission.query.get(mission_id)
        if not mission:
            return None
        
        pending = UserMission.query.filter_by(mission_id=mission_id, status='pending').count()
        completed = UserMission.query.filter_by(mission_id=mission_id, status='completed').count()
        rejected = UserMission.query.filter_by(mission_id=mission_id, status='rejected').count()
        
        return {
            'total': pending + completed + rejected,
            'pending': pending,
            'completed': completed,
            'rejected': rejected
        }
    
    @staticmethod
    def get_user_mission_stats(user_id):
        """Get user's mission completion stats"""
        try:
            completed = UserMission.query.filter_by(
                user_id=user_id,
                status='completed'
            ).count()

            pending = UserMission.query.filter_by(
                user_id=user_id,
                status='pending'
            ).count()
        except Exception:
            db.session.rollback()
            completed = 0
            pending = 0

        return {
            'completed': completed,
            'pending': pending
        }
