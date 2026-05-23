"""
Feed Routes
4chan-style board posts with threaded replies. - NO LOGIN REQUIRED FOR PUBLIC PAGES
"""
import hashlib
import time
from datetime import datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required, current_user
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.extensions import cache, db
from app.models import Post, PostInteraction, User
from app.utils import save_uploaded_image_optimized
from app.performance import CACHE_TIMEOUTS, cache_with_user_hash
from app.services.pagination_service import PaginationService
from app.services.wallet_service import WalletService
from app.validators import ValidationError

feed_bp = Blueprint('feed', __name__)
THREAD_POST_COST = 100
FEED_BATCH_SIZE = 20


def generate_post_number():
    """Generate a unique post number (8-digit)."""
    timestamp = int(time.time() * 1000)
    random_suffix = int.from_bytes(hashlib.md5(str(timestamp).encode()).digest()[:2], 'big')
    return str((timestamp % 100000000) + random_suffix)[:8].zfill(8)


def _can_create_thread(user):
    if not user or not user.is_authenticated:
        return False
    return int(user.coins or 0) >= THREAD_POST_COST


def _get_thread_root(post):
    """Walk parents until we reach the OP thread post."""
    current = post
    visited = set()
    while current and current.parent_id and current.id not in visited:
        visited.add(current.id)
        parent = Post.query.get(current.parent_id)
        if not parent:
            break
        current = parent
    return current


def _collect_thread_replies(root_id):
    """Collect replies recursively (reply-to-reply supported)."""
    replies = []
    frontier = [root_id]
    seen = {root_id}

    while frontier:
        children = Post.query.options(joinedload(Post.author))\
            .filter(Post.parent_id.in_(frontier))\
            .order_by(Post.created_at.asc())\
            .all()

        next_frontier = []
        for child in children:
            if child.id in seen:
                continue
            seen.add(child.id)
            replies.append(child)
            next_frontier.append(child.id)
        frontier = next_frontier

    replies.sort(key=lambda p: p.created_at or datetime.min)
    return replies


def _build_reply_depth_map(root_id, replies):
    by_parent = {}
    for reply in replies:
        by_parent.setdefault(reply.parent_id, []).append(reply)

    depth = {}
    stack = [(root_id, 0)]
    while stack:
        parent_id, level = stack.pop()
        for child in by_parent.get(parent_id, []):
            child_level = level + 1
            depth[child.id] = child_level
            stack.append((child.id, child_level))
    return depth


def _collect_descendant_ids(post_id):
    """Return all descendant post IDs for safe subtree deletion."""
    descendants = []
    frontier = [post_id]
    seen = {post_id}

    while frontier:
        rows = Post.query.with_entities(Post.id).filter(Post.parent_id.in_(frontier)).all()
        next_frontier = []
        for (child_id,) in rows:
            if child_id in seen:
                continue
            seen.add(child_id)
            descendants.append(child_id)
            next_frontier.append(child_id)
        frontier = next_frontier

    return descendants


def _query_thread_posts(page: int, per_page: int = FEED_BATCH_SIZE):
    """Paginated OP posts with efficient LIMIT/OFFSET query."""
    return PaginationService.paginate(
        Post.query.options(joinedload(Post.author))
        .filter(Post.parent_id.is_(None))
        .order_by(Post.created_at.desc(), Post.id.desc()),
        page=page,
        per_page=per_page,
    )


@feed_bp.route('/')
@cache_with_user_hash(cache, timeout=CACHE_TIMEOUTS['feed'], key_prefix='feed_index')
def index():
    """Board home - show only OP posts (threads)."""
    page = request.args.get('page', 1, type=int)
    posts = _query_thread_posts(page=page)

    return render_template(
        'feed/index.html',
        posts=posts,
        feed_batch_size=FEED_BATCH_SIZE,
        can_create_thread=_can_create_thread(current_user),
        thread_post_cost=THREAD_POST_COST
    )


@feed_bp.route('/api/posts')
def api_posts():
    """API endpoint for paginated posts."""
    params = PaginationService.get_page_args(
        page=request.args.get('page', 1, type=int),
        per_page=request.args.get('limit', FEED_BATCH_SIZE, type=int),
    )
    posts = _query_thread_posts(page=params.page, per_page=params.per_page)
    post_ids = [post.id for post in posts.items]
    reply_counts = {}
    if post_ids:
        reply_counts = dict(
            db.session.query(Post.parent_id, func.count(Post.id))
            .filter(Post.parent_id.in_(post_ids))
            .group_by(Post.parent_id)
            .all()
        )
    
    # Convert posts to JSON-serializable format
    posts_data = []
    for post in posts.items:
        author_data = None
        if post.author:
            author_data = {
                'id': post.author.id,
                'username': post.author.username,
                'profile_pic': post.author.profile_pic,
                'user_6digit': post.author.user_6digit
            }
        
        posts_data.append({
            'id': post.id,
            'content': post.content,
            'image_path': post.image_path,
            'created_at': post.created_at.isoformat() if post.created_at else None,
            'author': author_data,
            'post_number': post.post_number,
            'reply_count': int(reply_counts.get(post.id, 0))
        })
    
    return jsonify({
        'posts': posts_data,
        'page': posts.page,
        'pages': posts.pages,
        'total': posts.total,
        'has_next': posts.has_next,
        'has_prev': posts.has_prev,
        'next_page': posts.next_num if posts.has_next else None,
        'prev_page': posts.prev_num if posts.has_prev else None
    })


@feed_bp.route('/create', methods=['POST'])


@login_required
def create():
    """Create new thread post or reply."""
    # Check if user is logged in
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    content = request.form.get('content', '').strip()
    photo = request.files.get('photo')
    parent_id = request.form.get('parent_id', type=int, default=None)

    if not content:
        flash('Post content is required', 'error')
        return redirect(url_for('feed.index'))

    parent_post = None
    if parent_id is not None:
        parent_post = Post.query.get(parent_id)
        if not parent_post:
            flash('Reply target not found', 'error')
            return redirect(url_for('feed.index'))
    elif not _can_create_thread(current_user):
        flash(f'You need at least {THREAD_POST_COST} TNNO to create a thread.', 'error')
        return redirect(url_for('feed.index'))

    image_path = None
    if photo and photo.filename:
        try:
            image_path = save_uploaded_image_optimized(photo, 'posts')
        except ValueError as exc:
            flash(str(exc), 'error')
            return redirect(url_for('feed.index'))

    post = Post(
        user_id=current_user.id,
        content=content,
        image_path=image_path,
        parent_id=parent_id,
        post_number=generate_post_number()
    )

    if parent_post is None:
        try:
            WalletService.debit_user(
                user_id=current_user.id,
                amount=THREAD_POST_COST,
                transaction_type='thread_post_fee',
                details=f'post:{post.post_number}',
            )
        except ValidationError:
            db.session.rollback()
            flash(f'You need at least {THREAD_POST_COST} TNNO to create a thread.', 'error')
            return redirect(url_for('feed.index'))

    db.session.add(post)
    db.session.commit()

    cache.clear()

    if parent_post:
        root_post = _get_thread_root(parent_post)
        flash('Reply posted!', 'success')
        return redirect(url_for('feed.view', post_id=root_post.id, reply_to=(post.post_number or post.id)))

    flash(f'Thread created! {THREAD_POST_COST} TNNO deducted.', 'success')
    return redirect(url_for('feed.index'))


@feed_bp.route('/<int:post_id>')
@cache_with_user_hash(cache, timeout=CACHE_TIMEOUTS['feed'], key_prefix='feed_view')
def view(post_id):
    """View thread detail with all nested replies."""
    requested_post = Post.query.options(joinedload(Post.author)).get_or_404(post_id)
    root_post = _get_thread_root(requested_post)
    if not root_post:
        flash('Thread not found', 'error')
        return redirect(url_for('feed.index'))

    # Ensure author is loaded for the root too.
    root_post = Post.query.options(joinedload(Post.author)).get_or_404(root_post.id)

    replies = _collect_thread_replies(root_post.id)
    reply_depths = _build_reply_depth_map(root_post.id, replies)

    liked = False
    if current_user.is_authenticated:
        like = PostInteraction.query.filter_by(
            post_id=root_post.id,
            user_id=current_user.id,
            interaction_type='like'
        ).first()
        liked = like is not None

    return render_template(
        'feed/view.html',
        post=root_post,
        replies=replies,
        reply_depths=reply_depths,
        liked=liked
    )


@feed_bp.route('/<int:post_id>/like', methods=['POST'])


@login_required
def like(post_id):
    """Like/unlike post"""
    # Check if user is logged in
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    post = Post.query.get_or_404(post_id)

    existing_like = PostInteraction.query.filter_by(
        post_id=post_id,
        user_id=current_user.id,
        interaction_type='like'
    ).first()

    if existing_like:
        db.session.delete(existing_like)
        flash('Post unliked', 'info')
    else:
        like_obj = PostInteraction(
            post_id=post_id,
            user_id=current_user.id,
            interaction_type='like'
        )
        db.session.add(like_obj)
        flash('Post liked!', 'success')

    db.session.commit()
    cache.clear()

    root_post = _get_thread_root(post)
    return redirect(url_for('feed.view', post_id=root_post.id if root_post else post_id))


@feed_bp.route('/<int:post_id>/comment', methods=['POST'])


@login_required
def comment(post_id):
    """Legacy comment endpoint - stores as threaded reply post."""
    # Check if user is logged in
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    parent_post = Post.query.get_or_404(post_id)
    comment_text = request.form.get('comment', '').strip()

    if not comment_text:
        flash('Comment cannot be empty', 'error')
        root_post = _get_thread_root(parent_post)
        return redirect(url_for('feed.view', post_id=root_post.id if root_post else post_id))

    reply = Post(
        user_id=current_user.id,
        content=comment_text,
        parent_id=parent_post.id,
        post_number=generate_post_number()
    )
    db.session.add(reply)
    db.session.commit()

    cache.clear()
    flash('Reply added!', 'success')
    root_post = _get_thread_root(parent_post)
    return redirect(url_for('feed.view', post_id=root_post.id if root_post else post_id, reply_to=(reply.post_number or reply.id)))


@feed_bp.route('/<int:post_id>/reply', methods=['POST'])


@login_required
def reply(post_id):
    """Quick reply endpoint."""
    # Check if user is logged in
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    parent_post = Post.query.get_or_404(post_id)
    content = request.form.get('content', '').strip()
    photo = request.files.get('photo')

    if not content:
        flash('Reply content is required', 'error')
        root_post = _get_thread_root(parent_post)
        return redirect(url_for('feed.view', post_id=root_post.id if root_post else post_id))

    image_path = None
    if photo and photo.filename:
        try:
            image_path = save_uploaded_image_optimized(photo, 'posts')
        except ValueError as exc:
            flash(str(exc), 'error')
            root_post = _get_thread_root(parent_post)
            return redirect(url_for('feed.view', post_id=root_post.id if root_post else post_id))

    reply_post = Post(
        user_id=current_user.id,
        content=content,
        image_path=image_path,
        parent_id=parent_post.id,
        post_number=generate_post_number()
    )
    db.session.add(reply_post)
    db.session.commit()

    cache.clear()
    flash('Reply added!', 'success')
    root_post = _get_thread_root(parent_post)
    return redirect(url_for('feed.view', post_id=root_post.id if root_post else post_id, reply_to=(reply_post.post_number or reply_post.id)))


@feed_bp.route('/<int:post_id>/delete', methods=['POST'])


@login_required
def delete(post_id):
    """Delete post and its descendant replies."""
    # Check if user is logged in
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    post = Post.query.get_or_404(post_id)

    if post.user_id != current_user.id and not current_user.is_admin():
        flash('You can only delete your own posts', 'error')
        return redirect(url_for('feed.index'))

    root_for_redirect = _get_thread_root(post)
    redirect_thread_id = root_for_redirect.id if root_for_redirect else None

    descendant_ids = _collect_descendant_ids(post.id)
    if descendant_ids:
        Post.query.filter(Post.id.in_(descendant_ids)).delete(synchronize_session=False)

    db.session.delete(post)
    db.session.commit()

    cache.clear()
    flash('Post deleted', 'success')

    if post.parent_id and redirect_thread_id and redirect_thread_id != post.id:
        return redirect(url_for('feed.view', post_id=redirect_thread_id))
    return redirect(url_for('feed.index'))




