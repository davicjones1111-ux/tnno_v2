"""
Missions Routes
Mission listing, submissions, and management
"""
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app.extensions import db, cache
from app.models import Mission, UserMission
from app.services import MissionService
from app.services.history_service import HistoryService
from app.utils import save_uploaded_image_optimized
from sqlalchemy import or_

missions_bp = Blueprint('missions', __name__)


@missions_bp.route('/')
@login_required
def index():
    """Missions dashboard - list all active missions with pagination."""
    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 10

    try:
        query = Mission.query.filter_by(status='active')

        if search:
            query = query.filter(Mission.title.ilike(f'%{search}%'))

        missions_page = query.order_by(Mission.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
    except Exception:
        db.session.rollback()
        flash('Some mission data is still syncing. Please refresh in a moment.', 'info')
        missions_page = type('EmptyPagination', (), {'items': [], 'total': 0, 'pages': 0, 'has_next': False, 'has_prev': False})()

    try:
        user_submissions = UserMission.query.filter_by(
            user_id=current_user.id
        ).filter(UserMission.is_archived.is_(False))\
         .order_by(UserMission.submission_time.desc())\
         .limit(200)\
         .all()
    except Exception:
        db.session.rollback()
        user_submissions = UserMission.query.filter_by(
            user_id=current_user.id
        ).order_by(UserMission.submission_time.desc())\
         .limit(200)\
         .all()

    submission_by_mission = {}
    for sub in user_submissions:
        if sub.mission_id not in submission_by_mission:
            submission_by_mission[sub.mission_id] = sub

    user_stats = MissionService.get_user_mission_stats(current_user.id)
    
    return render_template('missions/index.html',
                         missions=missions_page.items,
                         submission_by_mission=submission_by_mission,
                         recent_submissions=user_submissions[:5],
                         stats=user_stats,
                         pagination=missions_page,
                         search=search)


@missions_bp.route('/api/missions')
@login_required
def api_missions():
    """API endpoint for missions with pagination and search."""
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 10, type=int)
    search = request.args.get('search', '').strip()
    
    # Enforce limits
    page = max(1, page)
    limit = min(50, max(1, limit))
    
    try:
        query = Mission.query.filter_by(status='active')

        if search:
            query = query.filter(Mission.title.ilike(f'%{search}%'))

        missions_page = query.order_by(Mission.created_at.desc()).paginate(
            page=page, per_page=limit, error_out=False
        )
    except Exception:
        db.session.rollback()
        return jsonify({
            'missions': [],
            'page': page,
            'limit': limit,
            'total': 0,
            'pages': 0,
            'has_next': False,
            'has_prev': False
        })

    try:
        submissions = UserMission.query.filter_by(user_id=current_user.id)\
            .filter(UserMission.is_archived.is_(False))\
            .order_by(UserMission.submission_time.desc())\
            .all()
    except Exception:
        db.session.rollback()
        submissions = UserMission.query.filter_by(user_id=current_user.id)\
            .order_by(UserMission.submission_time.desc())\
            .all()
    submission_by_mission = {}
    for sub in submissions:
        if sub.mission_id not in submission_by_mission:
            submission_by_mission[sub.mission_id] = sub
    
    return jsonify({
        'missions': [{
            'id': m.id,
            'title': m.title,
            'instructions': m.instructions,
            'reward': m.reward,
            'limit_count': m.limit_count,
            'time_limit': m.time_limit,
            'mission_type': m.mission_type,
            'image_path': m.image_path,
            'created_at': m.created_at.isoformat() if m.created_at else None,
            'submission_status': submission_by_mission.get(m.id).status if submission_by_mission.get(m.id) else None
        } for m in missions_page.items],
        'page': page,
        'limit': limit,
        'total': missions_page.total,
        'pages': missions_page.pages,
        'has_next': missions_page.has_next,
        'has_prev': missions_page.has_prev
    })


@missions_bp.route('/<int:mission_id>')
@login_required
def view(mission_id):
    """View mission details"""
    mission = MissionService.get_mission_by_id(mission_id)
    if not mission:
        flash('Mission not found', 'error')
        return redirect(url_for('missions.index'))
    
    # Check if user already submitted
    user_submission = UserMission.query.filter_by(
        user_id=current_user.id,
        mission_id=mission_id
    ).order_by(UserMission.submission_time.desc()).first()
    
    return render_template('missions/view.html',
                         mission=mission,
                         submission=user_submission)


@missions_bp.route('/<int:mission_id>/submit', methods=['POST'])
@login_required
def submit(mission_id):
    """Submit mission proof"""
    mission = MissionService.get_mission_by_id(mission_id)
    if not mission:
        flash('Mission not found', 'error')
        return redirect(url_for('missions.index'))
    
    # Get submission data
    code = request.form.get('code', '').strip()
    photo = request.files.get('photo')
    
    # Handle file upload
    photo_path = None
    if photo and photo.filename:
        try:
            photo_path = save_uploaded_image_optimized(photo, 'missions')
        except ValueError as exc:
            flash(str(exc), 'error')
            return redirect(url_for('missions.view', mission_id=mission_id))
    
    # Submit mission
    submission, message = MissionService.submit_mission(
        current_user.id,
        mission_id,
        code=code if code else None,
        photo_path=photo_path
    )
    
    if submission:
        flash('Mission submitted successfully! Waiting for approval.', 'success')
    else:
        flash(message, 'error')
    
    return redirect(url_for('missions.view', mission_id=mission_id))


@missions_bp.route('/my-submissions')
@login_required
def my_submissions():
    """View user's mission submissions"""
    HistoryService.archive_due_items(user_id=current_user.id)
    status = request.args.get('status')
    page = request.args.get('page', 1, type=int)
    submissions = MissionService.get_user_submissions(
        current_user.id,
        status=status,
        page=page,
        per_page=20
    )
    
    return render_template('missions/submissions.html',
                         submissions=submissions,
                         current_status=status)


@missions_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create():
    """Create new mission (admin only)"""
    if not current_user.is_admin():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))
    
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        instructions = request.form.get('instructions', '').strip()
        reward = request.form.get('reward', 0, type=int)
        limit_count = request.form.get('limit_count', 0, type=int)
        time_limit = request.form.get('time_limit', 24, type=int)
        mission_type = request.form.get('mission_type', 'default')
        image = request.files.get('image')
        
        if not title or not instructions:
            flash('Title and instructions are required', 'error')
            return render_template('missions/create.html')
        
        # Handle image upload
        image_path = None
        if image and image.filename:
            try:
                image_path = save_uploaded_image_optimized(image, 'missions')
            except ValueError as exc:
                flash(str(exc), 'error')
                return render_template('missions/create.html')
        
        mission = MissionService.create_mission(
            title=title,
            instructions=instructions,
            reward=reward,
            limit_count=limit_count,
            time_limit=time_limit,
            mission_type=mission_type,
            image_path=image_path
        )
        
        flash('Mission created successfully!', 'success')
        return redirect(url_for('missions.view', mission_id=mission.id))
    
    return render_template('missions/create.html')


@missions_bp.route('/<int:mission_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(mission_id):
    """Edit mission (admin only)"""
    if not current_user.is_admin():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))
    
    mission = MissionService.get_mission_by_id(mission_id)
    if not mission:
        flash('Mission not found', 'error')
        return redirect(url_for('missions.index'))
    
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        instructions = request.form.get('instructions', '').strip()
        reward = request.form.get('reward', 0, type=int)
        limit_count = request.form.get('limit_count', 0, type=int)
        time_limit = request.form.get('time_limit', 24, type=int)
        status = request.form.get('status', 'active')
        
        MissionService.update_mission(
            mission_id,
            title=title,
            instructions=instructions,
            reward=reward,
            limit_count=limit_count,
            time_limit=time_limit,
            status=status
        )
        
        flash('Mission updated successfully!', 'success')
        return redirect(url_for('missions.view', mission_id=mission_id))
    
    return render_template('missions/edit.html', mission=mission)


@missions_bp.route('/<int:mission_id>/delete', methods=['POST'])
@login_required
def delete(mission_id):
    """Delete mission (admin only)"""
    if not current_user.is_admin():
        flash('Access denied', 'error')
        return redirect(url_for('missions.index'))
    
    MissionService.delete_mission(mission_id)
    flash('Mission deleted successfully!', 'success')
    return redirect(url_for('missions.index'))
