"""
API Routes
REST API endpoints for AJAX and mobile access
"""
from flask import Blueprint, jsonify, request, url_for
from flask_login import login_required, current_user
from app.extensions import db
from app.models import User, Post, PostInteraction, GameScore
from app.services import UserService, MissionService, DepositService
from app.api_utils import error, ok, paginated, paginate_request_args
from app.services.pagination_service import PaginationService

api_bp = Blueprint('api', __name__)


# ==================== User API ====================

@api_bp.route('/user')
@login_required
def get_current_user():
    """Get current user data"""
    return jsonify(current_user.to_dict())


@api_bp.route('/user/<int:user_id>')

@login_required
def get_user(user_id):
    """Get user by ID"""
    if user_id != current_user.id and not current_user.is_admin():
        return error('User not found', 404)
    user = User.query.get(user_id)
    if not user:
        return error('User not found', 404)
    return jsonify(user.to_dict())


@api_bp.route('/leaderboard')

@login_required
def get_leaderboard():
    """Get TNNO leaderboard"""
    limit = request.args.get('limit', 10, type=int)
    leaders = UserService.get_leaderboard(limit=limit)
    return jsonify([{
        'rank': idx + 1,
        'user_id': u.id,
        'username': u.username,
        'coins': u.coins
    } for idx, u in enumerate(leaders)])


# ==================== Mission API ====================

@api_bp.route('/missions')
@login_required
def get_missions():
    """Get all active missions"""
    missions = MissionService.get_active_missions()
    return jsonify([m.to_dict() for m in missions])


@api_bp.route('/missions/<int:mission_id>')

@login_required
def get_mission(mission_id):
    """Get mission by ID"""
    mission = MissionService.get_mission_by_id(mission_id)
    if not mission:
        return jsonify({'error': 'Mission not found'}), 404
    return jsonify(mission.to_dict())


@api_bp.route('/missions/<int:mission_id>/submit', methods=['POST'])

@login_required
def submit_mission(mission_id):
    """Submit mission proof"""
    data = request.get_json() or {}
    code = data.get('code')
    photo_url = data.get('photo_url')
    
    submission, message = MissionService.submit_mission(
        current_user.id,
        mission_id,
        code=code,
        photo_path=photo_url
    )
    
    if submission:
        return ok({'message': message, 'submission': submission.to_dict()})
    return error(message, 400)


@api_bp.route('/my-missions')

@login_required
def get_my_missions():
    """Get user's mission submissions"""
    status = request.args.get('status')
    params = paginate_request_args(request, default_per_page=20)
    submissions = MissionService.get_user_submissions(
        current_user.id,
        status=status,
        page=params.page,
        per_page=params.per_page
    )
    return paginated(submissions, serializer=lambda s: s.to_dict())


# ==================== Feed API ====================

@api_bp.route('/feed')
@login_required
def get_feed():
    """Get social feed"""
    params = paginate_request_args(request, default_per_page=20)
    posts = PaginationService.paginate(
        Post.query.order_by(Post.created_at.desc(), Post.id.desc()),
        page=params.page,
        per_page=params.per_page,
    )
    return paginated(posts, key='posts', serializer=lambda p: p.to_dict())


@api_bp.route('/feed', methods=['POST'])

@login_required
def create_post():
    """Create new post"""
    data = request.get_json() or {}
    content = data.get('content', '').strip()
    image_url = data.get('image_url')
    
    if not content:
        return error('Content is required', 400)
    
    post = Post(
        user_id=current_user.id,
        content=content,
        image_path=image_url
    )
    db.session.add(post)
    db.session.commit()
    
    return ok({'post': post.to_dict()}, 201)


@api_bp.route('/feed/<int:post_id>/like', methods=['POST'])

@login_required
def like_post(post_id):
    """Like/unlike post"""
    existing = PostInteraction.query.filter_by(
        post_id=post_id,
        user_id=current_user.id,
        interaction_type='like'
    ).first()
    
    if existing:
        db.session.delete(existing)
        db.session.commit()
        return ok({'liked': False})
    
    like = PostInteraction(
        post_id=post_id,
        user_id=current_user.id,
        interaction_type='like'
    )
    db.session.add(like)
    db.session.commit()
    
    return ok({'liked': True})


@api_bp.route('/feed/<int:post_id>/comment', methods=['POST'])

@login_required
def comment_post(post_id):
    """Add comment to post"""
    data = request.get_json() or {}
    comment_text = data.get('comment', '').strip()
    
    if not comment_text:
        return error('Comment is required', 400)
    
    comment = PostInteraction(
        post_id=post_id,
        user_id=current_user.id,
        interaction_type='comment',
        comment=comment_text
    )
    db.session.add(comment)
    db.session.commit()
    
    return ok({'comment': {
        'id': comment.id,
        'user_id': comment.user_id,
        'username': current_user.username,
        'comment': comment.comment,
        'created_at': comment.created_at.isoformat()
    }}, 201)


# ==================== Deposit API ====================

@api_bp.route('/deposits')
@login_required
def get_deposits():
    """Get user's deposits"""
    status = request.args.get('status')
    params = paginate_request_args(request, default_per_page=20)
    deposits = DepositService.get_user_deposits(
        current_user.id,
        status=status,
        page=params.page,
        per_page=params.per_page
    )
    return paginated(deposits, serializer=lambda d: d.to_dict())


@api_bp.route('/deposits', methods=['POST'])

@login_required
def create_deposit():
    """Create deposit request"""
    data = request.get_json() or {}
    raw_amount = data.get('usdt_amount', '')

    try:
        deposit = DepositService.create_deposit(current_user.id, raw_amount)
    except ValueError as exc:
        return error(str(exc), 400)

    return jsonify({
        'success': True,
        'deposit': deposit.to_dict(),
        'payment_url': url_for('deposit.view', deposit_id=deposit.id),
    }), 201


# ==================== Game API ====================

@api_bp.route('/game/score', methods=['POST'])
@login_required
def save_game_score():
    """Save game score"""
    data = request.get_json() or {}
    score = data.get('score', 0, type=int)
    game_id = data.get('game_id', 'emperors_circle')
    
    if score <= 0:
        return jsonify({'error': 'Invalid score'}), 400
    
    game_score = UserService.save_game_score(current_user.id, score, game_id)
    
    return jsonify({'success': True, 'score': game_score.to_dict()})


@api_bp.route('/game/leaderboard')

@login_required
def get_game_leaderboard():
    """Get game leaderboard"""
    game_id = request.args.get('game_id', 'emperors_circle')
    limit = request.args.get('limit', 10, type=int)
    
    scores = UserService.get_game_leaderboard(game_id, limit)
    return jsonify([{
        'rank': idx + 1,
        'user_id': s.user_id,
        'username': s.user.username if s.user else 'Unknown',
        'score': s.score
    } for idx, s in enumerate(scores)])


# ==================== Stats API ====================

@api_bp.route('/stats')
@login_required
def get_stats():
    """Get user statistics"""
    stats = UserService.get_user_stats(current_user.id)
    if not stats:
        return jsonify({'error': 'User not found'}), 404
    
    return jsonify({
        'completed_missions': stats['completed_missions'],
        'total_posts': stats['total_posts'],
        'total_deposits': stats['total_deposits'],
'total_withdraws': stats['total_withdraws'],
        'best_game_score': stats['best_game_score']
    })


