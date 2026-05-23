"""
Merch Store Routes
Digital product store with file delivery
"""
import os
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app.extensions import db, cache
from app.models import Product, ProductFile, ProductImage, ProductRating, ProductReaction, ProductReview, MerchOrder, User, SellerRating, SellerReport
from app.models import SellerChatConversation, SellerChatMessage, SellerNotification
from app.datetime_utils import utc_now
from app.services.seller_service import SELLER_PLANS
from app.services.history_service import HistoryService
from app.services.pagination_service import PaginationService
from app.services.wallet_service import WalletService
from app.route_modules.marketplace_chat import register_marketplace_chat_routes
from app.route_modules.marketplace_orders import auto_cancel_overdue_physical_order, register_marketplace_order_routes
from app.utils import resolve_upload_path, save_uploaded_file_any, save_uploaded_image_optimized
from sqlalchemy import func, or_, and_
from sqlalchemy.orm import joinedload
from app.validators import ValidationError, validate_external_url

merch_bp = Blueprint('merch', __name__)

# Allowed extensions for uploads
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'zip', 'rar', 'pdf', 'txt', 'doc', 'docx', 'mp3', 'mp4', 'avi', 'mov'}
ETA_SET_DEADLINE_DAYS = 3
ETA_MAX_DAYS = 30
CANCEL_REFUND_RATE = 0.50
CANCEL_SELLER_RATE = 0.30
DELETED_PRODUCT_MARKER = '__deleted__'
MIN_PRODUCT_IMAGES = 1
MAX_PRODUCT_IMAGES = 3

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_merch_file(file, subfolder='merch'):
    """Save uploaded file to merch folder"""
    if not file or not file.filename:
        return None

    stored_path = save_uploaded_file_any(file, subfolder, ALLOWED_EXTENSIONS, allow_remote=False)
    return stored_path.rsplit('/', 1)[-1] if stored_path else None


def _uploaded_product_images(uploaded_images):
    """Return only non-empty uploaded product images."""
    return [image for image in uploaded_images if image and image.filename]


def _save_product_gallery_images(uploaded_images, subfolder='merch'):
    """Save up to three uploaded product images and return stored filenames."""
    saved_filenames = []
    for image in uploaded_images:
        if not image or not image.filename:
            continue
        image_path = save_uploaded_image_optimized(image, subfolder)
        image_filename = image_path.split('/')[-1] if image_path else None
        if image_filename and image_filename not in saved_filenames:
            saved_filenames.append(image_filename)
        if len(saved_filenames) >= MAX_PRODUCT_IMAGES:
            break
    return saved_filenames


def _sync_product_gallery(product, image_filenames):
    """Persist the cover image and extra gallery images for a product."""
    normalized_filenames = []
    for filename in image_filenames:
        if filename and filename not in normalized_filenames:
            normalized_filenames.append(filename)
        if len(normalized_filenames) >= MAX_PRODUCT_IMAGES:
            break

    previous_filenames = product.gallery_filenames
    product.image_filename = normalized_filenames[0] if normalized_filenames else None
    product.images.delete()
    db.session.flush()

    for index, image_filename in enumerate(normalized_filenames[1:], start=1):
        db.session.add(ProductImage(
            product_id=product.id,
            image_filename=image_filename,
            sort_order=index
        ))

    return [filename for filename in previous_filenames if filename and filename not in normalized_filenames]

def delete_merch_file(filename, subfolder='merch'):
    """Delete a stored merch file safely (best-effort)."""
    if not filename:
        return
    try:
        filepath = resolve_upload_path(filename, subfolder=subfolder)
        if filepath.exists():
            filepath.unlink()
    except Exception:
        # Swallow filesystem errors; DB state will be the source of truth.
        pass


def _seller_active(user):
    if not user:
        return True
    return bool(user.can_sell)


def _seller_rating_summary(seller_id):
    try:
        avg_rating, rating_count = db.session.query(
            func.coalesce(func.avg(SellerRating.rating), 0),
            func.count(SellerRating.id)
        ).filter(SellerRating.seller_id == seller_id).first()
        return float(avg_rating or 0), int(rating_count or 0)
    except Exception:
        db.session.rollback()
        return 0.0, 0


def _product_feedback_summary(product_id):
    try:
        avg_rating, rating_count = db.session.query(
            func.coalesce(func.avg(ProductRating.rating), 0),
            func.count(ProductRating.id)
        ).filter(ProductRating.product_id == product_id).first()

        reaction_rows = db.session.query(
            ProductReaction.reaction_type,
            func.count(ProductReaction.id)
        ).filter(ProductReaction.product_id == product_id)\
         .group_by(ProductReaction.reaction_type)\
         .all()
        reaction_map = {row[0]: int(row[1]) for row in reaction_rows}

        review_count = db.session.query(func.count(ProductReview.id))\
            .filter(ProductReview.product_id == product_id)\
            .scalar() or 0

        return {
            'avg_rating': float(avg_rating or 0),
            'rating_count': int(rating_count or 0),
            'like_count': int(reaction_map.get('like', 0)),
            'dislike_count': int(reaction_map.get('dislike', 0)),
            'review_count': int(review_count or 0)
        }
    except Exception:
        db.session.rollback()
        return {
            'avg_rating': 0.0,
            'rating_count': 0,
            'like_count': 0,
            'dislike_count': 0,
            'review_count': 0
        }


def _product_feedback_payload(product_id):
    summary = _product_feedback_summary(product_id)
    try:
        user_rating = ProductRating.query.filter_by(product_id=product_id, user_id=current_user.id).first()
        user_reaction = ProductReaction.query.filter_by(product_id=product_id, user_id=current_user.id).first()
    except Exception:
        db.session.rollback()
        user_rating = None
        user_reaction = None
    summary['user_rating'] = int(user_rating.rating) if user_rating else 0
    summary['user_reaction'] = user_reaction.reaction_type if user_reaction else ''
    return summary


def _product_feedback_map(product_ids):
    """Return compact feedback stats for many products at once."""
    product_ids = [pid for pid in set(product_ids or []) if pid]
    if not product_ids:
        return {}

    feedback_map = {
        pid: {
            'avg_rating': 0.0,
            'rating_count': 0,
            'like_count': 0,
            'dislike_count': 0,
            'review_count': 0
        }
        for pid in product_ids
    }

    try:
        ratings = db.session.query(
            ProductRating.product_id,
            func.coalesce(func.avg(ProductRating.rating), 0).label('avg_rating'),
            func.count(ProductRating.id).label('rating_count')
        ).filter(ProductRating.product_id.in_(product_ids))\
         .group_by(ProductRating.product_id)\
         .all()

        reactions = db.session.query(
            ProductReaction.product_id,
            ProductReaction.reaction_type,
            func.count(ProductReaction.id).label('reaction_count')
        ).filter(ProductReaction.product_id.in_(product_ids))\
         .group_by(ProductReaction.product_id, ProductReaction.reaction_type)\
         .all()

        reviews = db.session.query(
            ProductReview.product_id,
            func.count(ProductReview.id).label('review_count')
        ).filter(ProductReview.product_id.in_(product_ids))\
         .group_by(ProductReview.product_id)\
         .all()

        for row in ratings:
            feedback_map[row.product_id]['avg_rating'] = float(row.avg_rating or 0)
            feedback_map[row.product_id]['rating_count'] = int(row.rating_count or 0)

        for row in reactions:
            key = 'like_count' if row.reaction_type == 'like' else 'dislike_count'
            feedback_map[row.product_id][key] = int(row.reaction_count or 0)

        for row in reviews:
            feedback_map[row.product_id]['review_count'] = int(row.review_count or 0)
    except Exception:
        db.session.rollback()

    return feedback_map


def _apply_store_filters(query, search='', seller_search='', product_type=''):
    """Apply shared store filters for page and API results."""
    if product_type in {'digital', 'physical'}:
        query = query.filter(Product.product_type == product_type)

    if search:
        query = query.filter(Product.name.ilike(f'%{search}%'))

    if seller_search:
        seller_filters = [User.username.ilike(f'%{seller_search}%')]
        if seller_search.isdigit():
            seller_filters.append(User.id == int(seller_search))
        query = query.join(User, Product.seller_id == User.id).filter(or_(*seller_filters))

    return query

def _calculate_cancel_split(total_price: int) -> tuple[int, int, int]:
    total = max(int(total_price or 0), 0)
    buyer_refund = int(round(total * CANCEL_REFUND_RATE))
    seller_payout = int(round(total * CANCEL_SELLER_RATE))
    fee_amount = total - buyer_refund - seller_payout
    return buyer_refund, seller_payout, fee_amount

def _attach_cancel_metadata(order: MerchOrder, now: datetime) -> None:
    purchased_at = order.purchased_at or now
    eta_deadline = purchased_at + timedelta(days=ETA_SET_DEADLINE_DAYS)
    order._eta_set_deadline = eta_deadline
    order._eta_set_deadline_passed = now >= eta_deadline
    order._cancel_policy = 'none'
    if order.status == 'pending':
        if order.delivery_eta:
            order._cancel_policy = 'penalty' if now < order.delivery_eta else 'free'
        else:
            order._cancel_policy = 'free' if now >= eta_deadline else 'blocked'
    buyer_refund, seller_payout, fee_amount = _calculate_cancel_split(order.total_price)
    order._cancel_refund = buyer_refund
    order._cancel_seller = seller_payout
    order._cancel_fee = fee_amount


# ==================== USER ROUTES ====================

@merch_bp.route('/')

@login_required
def index():
    """Merch store home - display all products"""
    search = request.args.get('search', '').strip()
    seller_search = request.args.get('seller', '').strip()
    product_type = (request.args.get('type') or '').strip().lower()
    sort = request.args.get('sort', 'latest').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 12
    
    seller_active_filter = or_(
        Product.seller_id.is_(None),
        User.role == 'admin',
        and_(User.is_seller.is_(True), User.seller_expires_at.isnot(None), User.seller_expires_at >= utc_now())
    )

    query = Product.query.outerjoin(User, User.id == Product.seller_id)\
        .options(joinedload(Product.seller))\
        .filter(Product.is_active.is_(True))\
        .filter(seller_active_filter)
    query = _apply_store_filters(query, search=search, seller_search=seller_search, product_type=product_type)
    
    # Sorting
    if sort == 'price_low':
        query = query.order_by(Product.price.asc())
    elif sort == 'price_high':
        query = query.order_by(Product.price.desc())
    elif sort == 'popular':
        query = query.outerjoin(MerchOrder).group_by(Product.id).order_by(db.func.count(MerchOrder.id).desc(), Product.created_at.desc())
    else:
        query = query.order_by(Product.created_at.desc())
    
    # Paginate
    products_page = query.paginate(page=page, per_page=per_page, error_out=False)
    products = products_page.items

    seller_ids = {p.seller_id for p in products if p.seller_id}
    rating_map = {}
    if seller_ids:
        rows = db.session.query(
            SellerRating.seller_id,
            func.coalesce(func.avg(SellerRating.rating), 0).label('avg_rating'),
            func.count(SellerRating.id).label('rating_count')
        ).filter(SellerRating.seller_id.in_(seller_ids))\
         .group_by(SellerRating.seller_id)\
         .all()
        rating_map = {row.seller_id: {'avg': float(row.avg_rating or 0), 'count': int(row.rating_count or 0)} for row in rows}
    product_feedbacks = _product_feedback_map([p.id for p in products])

    return render_template('merch/index.html', 
                         products=products, 
                         search=search,
                         seller_search=seller_search,
                         product_type=product_type,
                         sort=sort,
                         pagination=products_page,
                         seller_ratings=rating_map,
                         product_feedbacks=product_feedbacks)


@merch_bp.route('/api/products')
@login_required
def api_products():
    """API endpoint for products with pagination, filtering, and search."""
    page = request.args.get('page', 1, type=int)
    limit = min(50, max(1, request.args.get('limit', 12, type=int)))
    search = request.args.get('search', '').strip()
    seller_search = request.args.get('seller', '').strip()
    product_type = (request.args.get('type') or '').strip().lower()
    sort = request.args.get('sort', 'latest').strip()
    
    # Enforce limits
    page = max(1, page)
    
    seller_active_filter = or_(
        Product.seller_id.is_(None),
        User.role == 'admin',
        and_(User.is_seller.is_(True), User.seller_expires_at.isnot(None), User.seller_expires_at >= utc_now())
    )

    query = Product.query.outerjoin(User, User.id == Product.seller_id)\
        .options(joinedload(Product.seller))\
        .filter(Product.is_active.is_(True))\
        .filter(seller_active_filter)
    query = _apply_store_filters(query, search=search, seller_search=seller_search, product_type=product_type)

    # Sorting
    if sort == 'price_low':
        query = query.order_by(Product.price.asc(), Product.created_at.desc())
    elif sort == 'price_high':
        query = query.order_by(Product.price.desc(), Product.created_at.desc())
    elif sort == 'popular':
        query = query.outerjoin(MerchOrder).group_by(Product.id).order_by(func.count(MerchOrder.id).desc(), Product.created_at.desc())
    else:
        query = query.order_by(Product.created_at.desc())

    products_page = query.paginate(page=page, per_page=limit, error_out=False)
    total = products_page.total
    paginated = products_page.items
    
    product_feedbacks = _product_feedback_map([p.id for p in paginated])

    return jsonify({
        'products': [{
            'id': p.id,
            'name': p.name,
            'description': p.description,
            'price': p.price,
            'image_filename': p.image_filename,
            'product_type': p.product_type,
            'seller_id': p.seller_id,
            'quantity': p.quantity,
            'seller_username': p.seller.username if p.seller else None,
            'feedback': product_feedbacks.get(p.id, {
                'avg_rating': 0.0,
                'rating_count': 0,
                'like_count': 0,
                'dislike_count': 0,
                'review_count': 0
            })
        } for p in paginated],
        'page': page,
        'limit': limit,
        'total': total,
        'pages': products_page.pages,
        'has_next': products_page.has_next,
        'has_prev': page > 1
    })


@merch_bp.route('/product/<int:product_id>')


@login_required
def product_detail(product_id):
    """View product details"""
    product = Product.query.get_or_404(product_id)
    if product.seller_id is not None and not _seller_active(product.seller) and not current_user.is_admin():
        flash('This seller is inactive. Product is hidden.', 'error')
        return redirect(url_for('merch.index'))
    seller_rating = None
    seller_sales = None
    if product.seller_id:
        avg_rating, rating_count = _seller_rating_summary(product.seller_id)
        seller_rating = {'avg': avg_rating, 'count': rating_count}
        seller_sales = db.session.query(
            func.coalesce(func.sum(MerchOrder.total_price), 0)
        ).join(Product, Product.id == MerchOrder.product_id)\
         .filter(
             Product.seller_id == product.seller_id,
             MerchOrder.status.in_(['completed', 'delivered'])
         )\
         .scalar() or 0
    product_feedback = _product_feedback_payload(product.id)
    recent_reviews = ProductReview.query.filter_by(product_id=product.id)\
        .order_by(ProductReview.updated_at.desc())\
        .limit(2)\
        .all()
    return render_template(
        'merch/product.html',
        product=product,
        seller_rating=seller_rating,
        seller_sales=int(seller_sales or 0),
        product_feedback=product_feedback,
        recent_reviews=recent_reviews
    )


@merch_bp.route('/product/<int:product_id>/rate', methods=['POST'])
@login_required
def rate_product(product_id):
    """Rate a product with 1-5 stars."""
    product = Product.query.get_or_404(product_id)
    if product.seller_id == current_user.id:
        return jsonify({'ok': False, 'error': 'You cannot rate your own product.'}), 400

    rating = request.form.get('rating', type=int)
    if rating not in {1, 2, 3, 4, 5}:
        return jsonify({'ok': False, 'error': 'Rating must be between 1 and 5.'}), 400

    existing = ProductRating.query.filter_by(product_id=product.id, user_id=current_user.id).first()
    if existing:
        existing.rating = rating
    else:
        db.session.add(ProductRating(product_id=product.id, user_id=current_user.id, rating=rating))
    db.session.commit()

    return jsonify({'ok': True, 'feedback': _product_feedback_payload(product.id)})


@merch_bp.route('/product/<int:product_id>/react', methods=['POST'])
@login_required
def react_product(product_id):
    """Like or dislike a product without reloading the page."""
    product = Product.query.get_or_404(product_id)
    if product.seller_id == current_user.id:
        return jsonify({'ok': False, 'error': 'You cannot react to your own product.'}), 400

    reaction_type = (request.form.get('reaction_type') or '').strip().lower()
    if reaction_type not in {'like', 'dislike'}:
        return jsonify({'ok': False, 'error': 'Invalid reaction type.'}), 400

    existing = ProductReaction.query.filter_by(product_id=product.id, user_id=current_user.id).first()
    if existing and existing.reaction_type == reaction_type:
        db.session.delete(existing)
    elif existing:
        existing.reaction_type = reaction_type
    else:
        db.session.add(ProductReaction(product_id=product.id, user_id=current_user.id, reaction_type=reaction_type))
    db.session.commit()

    return jsonify({'ok': True, 'feedback': _product_feedback_payload(product.id)})


@merch_bp.route('/product/<int:product_id>/reviews')
@login_required
def product_reviews(product_id):
    """View and write product reviews."""
    product = Product.query.get_or_404(product_id)
    if product.seller_id is not None and not _seller_active(product.seller) and not current_user.is_admin():
        flash('This seller is inactive. Product is hidden.', 'error')
        return redirect(url_for('merch.index'))

    reviews = ProductReview.query.filter_by(product_id=product.id)\
        .order_by(ProductReview.updated_at.desc())\
        .all()
    user_review = ProductReview.query.filter_by(product_id=product.id, user_id=current_user.id).first()
    product_feedback = _product_feedback_payload(product.id)

    return render_template(
        'merch/product_reviews.html',
        product=product,
        reviews=reviews,
        user_review=user_review,
        product_feedback=product_feedback
    )


@merch_bp.route('/product/<int:product_id>/reviews', methods=['POST'])
@login_required
def save_product_review(product_id):
    """Create or update a product review."""
    product = Product.query.get_or_404(product_id)
    if product.seller_id == current_user.id:
        flash('You cannot review your own product.', 'error')
        return redirect(url_for('merch.product_reviews', product_id=product.id))

    title = (request.form.get('title') or '').strip()
    content = (request.form.get('content') or '').strip()
    if not content:
        flash('Review text is required.', 'error')
        return redirect(url_for('merch.product_reviews', product_id=product.id))

    existing = ProductReview.query.filter_by(product_id=product.id, user_id=current_user.id).first()
    if existing:
        existing.title = title or None
        existing.content = content
    else:
        db.session.add(ProductReview(
            product_id=product.id,
            user_id=current_user.id,
            title=title or None,
            content=content
        ))
    db.session.commit()

    flash('Review saved.', 'success')
    return redirect(url_for('merch.product_reviews', product_id=product.id))


@merch_bp.route('/seller/<int:seller_id>')
@login_required
def seller_profile(seller_id):
    """Public seller profile for ratings and stats."""
    seller = User.query.get_or_404(seller_id)
    if not seller.is_seller and not seller.is_admin():
        flash('Seller not found', 'error')
        return redirect(url_for('merch.index'))

    avg_rating, rating_count = _seller_rating_summary(seller_id)

    try:
        total_sales = db.session.query(
            func.coalesce(func.sum(MerchOrder.total_price), 0)
        ).join(Product, Product.id == MerchOrder.product_id)\
         .filter(
             Product.seller_id == seller_id,
             MerchOrder.status.in_(['completed', 'delivered'])
         )\
         .scalar() or 0
        total_items_sold = db.session.query(
            func.coalesce(func.sum(MerchOrder.quantity), 0)
        ).join(Product, Product.id == MerchOrder.product_id)\
         .filter(
             Product.seller_id == seller_id,
             MerchOrder.status.in_(['completed', 'delivered'])
         )\
         .scalar() or 0
    except Exception:
        db.session.rollback()
        total_sales = 0
        total_items_sold = 0

    try:
        user_rating = SellerRating.query.filter_by(
            seller_id=seller_id,
            rater_id=current_user.id
        ).first()
    except Exception:
        db.session.rollback()
        user_rating = None

    page = request.args.get('page', 1, type=int)
    params = PaginationService.get_page_args(page, 12)

    try:
        products_page = PaginationService.paginate(
            Product.query.options(joinedload(Product.seller))
            .filter_by(seller_id=seller_id, is_active=True)
            .order_by(Product.created_at.desc(), Product.id.desc()),
            page=params.page,
            per_page=params.per_page,
        )
        products = products_page.items
    except Exception:
        db.session.rollback()
        products_page = None
        products = []

    product_feedbacks = _product_feedback_map([p.id for p in products])

    return render_template(
        'merch/seller_profile.html',
        seller=seller,
        avg_rating=avg_rating,
        rating_count=rating_count,
        total_sales=int(total_sales),
        total_items_sold=int(total_items_sold),
        user_rating=user_rating.rating if user_rating else None,
        products=products,
        product_feedbacks=product_feedbacks,
        products_page=products_page
    )


@merch_bp.route('/seller/<int:seller_id>/rate', methods=['POST'])
@login_required
def rate_seller(seller_id):
    """Rate a seller (1-5)."""
    if seller_id == current_user.id:
        flash('You cannot rate yourself.', 'error')
        return redirect(url_for('merch.seller_profile', seller_id=seller_id))

    seller = User.query.get_or_404(seller_id)
    if not seller.is_seller and not seller.is_admin():
        flash('Seller not found', 'error')
        return redirect(url_for('merch.index'))

    rating = request.form.get('rating', type=int)
    if rating not in {1, 2, 3, 4, 5}:
        flash('Rating must be between 1 and 5.', 'error')
        return redirect(url_for('merch.seller_profile', seller_id=seller_id))

    existing = SellerRating.query.filter_by(seller_id=seller_id, rater_id=current_user.id).first()
    if existing:
        existing.rating = rating
    else:
        db.session.add(SellerRating(seller_id=seller_id, rater_id=current_user.id, rating=rating))
    db.session.commit()

    flash('Rating submitted.', 'success')
    return redirect(url_for('merch.seller_profile', seller_id=seller_id))


@merch_bp.route('/seller/<int:seller_id>/report', methods=['POST'])
@login_required
def report_seller(seller_id):
    """Report a seller to admin."""
    if seller_id == current_user.id:
        flash('You cannot report yourself.', 'error')
        return redirect(url_for('merch.seller_profile', seller_id=seller_id))

    seller = User.query.get_or_404(seller_id)
    if not seller.is_seller and not seller.is_admin():
        flash('Seller not found', 'error')
        return redirect(url_for('merch.index'))

    message = (request.form.get('message') or '').strip()
    evidence = request.files.get('evidence')
    if not message:
        flash('Report message is required.', 'error')
        return redirect(url_for('merch.seller_profile', seller_id=seller_id))

    evidence_path = None
    if evidence and evidence.filename:
        try:
            evidence_path = save_uploaded_image_optimized(evidence, 'seller_reports')
        except ValueError as exc:
            flash(str(exc), 'error')
            return redirect(url_for('merch.seller_profile', seller_id=seller_id))

    report = SellerReport(
        seller_id=seller_id,
        reporter_id=current_user.id,
        message=message,
        evidence_path=evidence_path,
        status='pending'
    )
    db.session.add(report)
    db.session.commit()

    flash('Report submitted. Admin will review it.', 'success')
    return redirect(url_for('merch.seller_profile', seller_id=seller_id))


register_marketplace_order_routes(
    merch_bp,
    seller_active_fn=_seller_active,
    attach_cancel_metadata_fn=_attach_cancel_metadata,
    calculate_cancel_split_fn=_calculate_cancel_split,
    eta_set_deadline_days=ETA_SET_DEADLINE_DAYS,
)


# ==================== ADMIN ROUTES ====================

@merch_bp.route('/admin/products')

@login_required
def admin_products():
    """Admin / seller: List products. Admin sees all, sellers see their own."""
    if not (current_user.is_admin() or current_user.is_seller):
        flash('Admin access required', 'error')
        return redirect(url_for('merch.index'))

    if not current_user.is_admin() and current_user.is_seller and not current_user.can_sell:
        flash('Seller plan expired. Renew to show products in the store.', 'error')
    
    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 30
    query = Product.query
    if not current_user.is_admin():
        query = query.filter_by(seller_id=current_user.id)
    if search:
        query = query.filter(Product.name.ilike(f'%{search}%'))
    products_page = query.filter(
        (Product.contact_link.is_(None)) | (Product.contact_link != DELETED_PRODUCT_MARKER)
    ).order_by(Product.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return render_template(
        'merch/admin_products.html',
        products=products_page.items,
        products_page=products_page,
        search=search
    )


@merch_bp.route('/admin/create', methods=['GET', 'POST'])


@login_required
def admin_create():
    """Admin / seller: Create new product"""
    if not (current_user.is_admin() or current_user.is_seller):
        flash('Admin access required', 'error')
        return redirect(url_for('merch.index'))
    
    if request.method == 'POST':
        if not current_user.is_admin() and not current_user.can_sell:
            flash('Seller plan expired. Please renew to add products.', 'error')
            return redirect(url_for('merch.admin_create'))
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        price = request.form.get('price', type=int, default=0)
        product_type = (request.form.get('product_type') or 'digital').strip().lower()
        contact_link = (request.form.get('contact_link') or '').strip()
        physical_quantity = request.form.get('physical_quantity', type=int, default=0)
        files = request.files.getlist('files')
        uploaded_images = _uploaded_product_images(request.files.getlist('images'))
        
        if not name:
            flash('Product name is required', 'error')
            return redirect(url_for('merch.admin_create'))
        
        if price < 1:
            flash('Price must be at least 1 TNNO', 'error')
            return redirect(url_for('merch.admin_create'))

        if product_type not in {'digital', 'physical'}:
            flash('Invalid product type', 'error')
            return redirect(url_for('merch.admin_create'))

        if product_type == 'physical':
            if physical_quantity < 1:
                flash('Physical quantity must be at least 1', 'error')
                return redirect(url_for('merch.admin_create'))
            if not contact_link:
                flash('Contact link is required for physical products', 'error')
                return redirect(url_for('merch.admin_create'))
            try:
                contact_link = validate_external_url(contact_link, field_name='Contact link')
            except ValidationError as exc:
                flash(str(exc), 'error')
                return redirect(url_for('merch.admin_create'))
        else:
            if not files or len(files) == 0 or not files[0].filename:
                flash('At least one product file is required', 'error')
                return redirect(url_for('merch.admin_create'))
        
        try:
            if len(uploaded_images) < MIN_PRODUCT_IMAGES:
                flash('Add 1 to 3 product photos. The first photo becomes the store cover.', 'error')
                return redirect(url_for('merch.admin_create'))

            if len(uploaded_images) > MAX_PRODUCT_IMAGES:
                flash('You can upload up to 3 product photos.', 'error')
                return redirect(url_for('merch.admin_create'))

            # Save product gallery if provided
            image_filenames = []
            try:
                image_filenames = _save_product_gallery_images(uploaded_images, 'merch')
            except ValueError as exc:
                flash(str(exc), 'error')
                return redirect(url_for('merch.admin_create'))

            if len(image_filenames) < MIN_PRODUCT_IMAGES:
                flash('At least one valid product photo is required.', 'error')
                return redirect(url_for('merch.admin_create'))
            
            # Create product
            product = Product(
                name=name,
                description=description,
                price=price,
                product_type=product_type,
                contact_link=contact_link if product_type == 'physical' else None,
                physical_quantity=physical_quantity if product_type == 'physical' else 0,
                seller_id=current_user.id if not current_user.is_admin() else None
            )
            db.session.add(product)
            db.session.flush()  # Get product ID
            _sync_product_gallery(product, image_filenames)
            
            saved_files = 0
            if product_type == 'digital':
                # Save product files (1 file = 1 quantity)
                for file in files:
                    if file and file.filename:
                        if allowed_file(file.filename):
                            try:
                                filename = save_merch_file(file, 'merch')
                            except ValueError as exc:
                                db.session.rollback()
                                flash(str(exc), 'error')
                                return redirect(url_for('merch.admin_create'))
                            if filename:
                                product_file = ProductFile(
                                    product_id=product.id,
                                    file_filename=filename,
                                    original_name=secure_filename(file.filename)
                                )
                                db.session.add(product_file)
                                saved_files += 1
                        else:
                            flash(f'File type not allowed: {file.filename}', 'warning')
                
                if saved_files == 0:
                    db.session.rollback()
                    flash('No valid files were uploaded', 'error')
                    return redirect(url_for('merch.admin_create'))
            
            db.session.commit()
            if product_type == 'physical':
                flash('Physical product created successfully!', 'success')
            else:
                flash(f'Product created successfully with {saved_files} files!', 'success')
            return redirect(url_for('merch.admin_products'))
            
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Product creation failed')
            flash('Unable to create product right now. Please try again.', 'error')
            return redirect(url_for('merch.admin_create'))
    
    return render_template(
        'merch/admin_create.html',
        seller_active=current_user.can_sell,
        seller_expires_at=current_user.seller_expires_at,
        seller_plans=SELLER_PLANS
    )


@merch_bp.route('/admin/edit/<int:product_id>', methods=['GET', 'POST'])


@login_required
def admin_edit(product_id):
    """Admin / seller: Edit product"""
    if not (current_user.is_admin() or current_user.is_seller):
        flash('Admin access required', 'error')
        return redirect(url_for('merch.index'))
    
    product = Product.query.get_or_404(product_id)
    # sellers may only modify their own products
    if not current_user.is_admin() and product.seller_id != current_user.id:
        flash('You do not have permission to edit this product', 'error')
        return redirect(url_for('merch.admin_products'))
    
    if request.method == 'POST':
        product.name = request.form.get('name', '').strip()
        product.description = request.form.get('description', '').strip()
        
        new_price = request.form.get('price', type=int, default=0)
        if new_price >= 1:
            product.price = new_price

        if product.product_type == 'physical':
            contact_link = (request.form.get('contact_link') or '').strip()
            physical_quantity = request.form.get('physical_quantity', type=int)
            if contact_link:
                try:
                    product.contact_link = validate_external_url(contact_link, field_name='Contact link')
                except ValidationError as exc:
                    flash(str(exc), 'error')
                    return redirect(url_for('merch.admin_edit', product_id=product.id))
            if physical_quantity is not None and physical_quantity >= 0:
                product.physical_quantity = physical_quantity
        
        # Handle gallery upload
        uploaded_images = _uploaded_product_images(request.files.getlist('images'))
        if len(uploaded_images) > MAX_PRODUCT_IMAGES:
            flash('You can upload up to 3 product photos.', 'error')
            return redirect(url_for('merch.admin_edit', product_id=product.id))

        gallery_files_to_delete = []
        if uploaded_images:
            try:
                new_filenames = _save_product_gallery_images(uploaded_images, 'merch')
                if len(new_filenames) < MIN_PRODUCT_IMAGES:
                    flash('Add at least one valid product photo.', 'error')
                    return redirect(url_for('merch.admin_edit', product_id=product.id))
                gallery_files_to_delete = _sync_product_gallery(product, new_filenames)
            except ValueError as exc:
                flash(str(exc), 'error')
                return redirect(url_for('merch.admin_edit', product_id=product.id))
        elif not product.gallery_filenames:
            flash('Add 1 to 3 product photos so the product has a store cover.', 'error')
            return redirect(url_for('merch.admin_edit', product_id=product.id))
        
        # Add more files
        if product.product_type != 'physical':
            new_files = request.files.getlist('new_files')
            if new_files and new_files[0].filename:
                for file in new_files:
                    if file and file.filename and allowed_file(file.filename):
                        try:
                            filename = save_merch_file(file, 'merch')
                        except ValueError as exc:
                            flash(str(exc), 'error')
                            return redirect(url_for('merch.admin_edit', product_id=product.id))
                        if filename:
                            product_file = ProductFile(
                                product_id=product.id,
                                file_filename=filename,
                                original_name=secure_filename(file.filename)
                            )
                            db.session.add(product_file)
        
        # Toggle active status
        product.is_active = 'is_active' in request.form
        
        db.session.commit()
        for filename in gallery_files_to_delete:
            delete_merch_file(filename, 'merch')
        flash('Product updated successfully!', 'success')
        return redirect(url_for('merch.admin_products'))
    
    return render_template('merch/admin_edit.html', product=product)


@merch_bp.route('/admin/delete/<int:product_id>', methods=['POST'])


@login_required
def admin_delete(product_id):
    """Admin / seller: Delete product"""
    if not (current_user.is_admin() or current_user.is_seller):
        flash('Admin access required', 'error')
        return redirect(url_for('merch.index'))
    
    product = Product.query.get_or_404(product_id)
    if not current_user.is_admin() and product.seller_id != current_user.id:
        flash('You do not have permission to delete this product', 'error')
        return redirect(url_for('merch.admin_products'))

    try:
        order_count = product.orders.count()
        active_buyer_order_count = product.orders.filter(
            MerchOrder.status.in_(['pending', 'completed', 'delivered'])
        ).count()

        if order_count > 0:
            product_type = (product.product_type or 'digital').lower()
            if product_type == 'digital':
                unsold_files = product.files.filter_by(is_sold=False).all()
                for pf in unsold_files:
                    delete_merch_file(pf.file_filename, 'merch')
                    db.session.delete(pf)
                product.is_active = False
                # Soft-delete marker: keep DB row for historical orders/downloads,
                # but hide from admin/seller products list.
                product.contact_link = DELETED_PRODUCT_MARKER
                db.session.commit()
                flash(
                    'Product has orders, so it was removed from sale and unsold files were deleted. '
                    'Sold files are kept for buyer downloads.',
                    'success'
                )
                return redirect(url_for('merch.admin_products'))

            if active_buyer_order_count == 0:
                product.is_active = False
                product.contact_link = DELETED_PRODUCT_MARKER
                db.session.commit()
                flash('Product removed from your store. Old refunded order history was kept safely.', 'success')
                return redirect(url_for('merch.admin_products'))

            flash('Cannot delete a product that still has buyers. Use Hide instead.', 'error')
            return redirect(url_for('merch.admin_products'))

        # Delete associated files from disk (thumbnail + product files)
        if product.image_filename:
            delete_merch_file(product.image_filename, 'merch')
        for gallery_image in product.images.all():
            delete_merch_file(gallery_image.image_filename, 'merch')
        for pf in product.files.all():
            delete_merch_file(pf.file_filename, 'merch')

        db.session.delete(product)
        db.session.commit()
        flash('Product deleted (image and files removed)', 'success')
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Product deletion failed')
        flash('Unable to delete the product right now. Please try again.', 'error')

    return redirect(url_for('merch.admin_products'))


@merch_bp.route('/admin/hide/<int:product_id>', methods=['POST'])
@login_required
def admin_hide(product_id):
    """Admin / seller: Hide or show a product without deleting it"""
    if not (current_user.is_admin() or current_user.is_seller):
        flash('Admin access required', 'error')
        return redirect(url_for('merch.index'))
    
    product = Product.query.get_or_404(product_id)
    if not current_user.is_admin() and product.seller_id != current_user.id:
        flash('You do not have permission to change this product', 'error')
        return redirect(url_for('merch.admin_products'))

    product.is_active = not product.is_active
    if not product.is_active:
        # Prevent new purchases: mark unsold digital files as sold
        if product.product_type != 'physical':
            for pf in product.files.filter_by(is_sold=False).all():
                pf.is_sold = True
    db.session.commit()
    flash('Product hidden from store' if not product.is_active else 'Product is visible in store', 'success')
    return redirect(url_for('merch.admin_products'))

@merch_bp.route('/admin/sales/<int:order_id>/eta', methods=['POST'])
@login_required
def set_delivery_eta(order_id):
    """Set delivery ETA for a physical order."""
    if not (current_user.is_admin() or current_user.is_seller):
        flash('Admin access required', 'error')
        return redirect(url_for('merch.index'))

    order = MerchOrder.query.get_or_404(order_id)
    order_type = (order.product_type or order.product.product_type or 'digital').lower()
    if order_type != 'physical':
        flash('This order is not a physical order', 'error')
        return redirect(url_for('merch.admin_sales'))

    if not current_user.is_admin() and order.product.seller_id != current_user.id:
        flash('You do not have permission to update this order', 'error')
        return redirect(url_for('merch.admin_sales'))

    if order.delivery_eta is not None:
        flash('Delivery ETA is already set and cannot be changed.', 'error')
        return redirect(url_for('merch.admin_sales'))

    if order.status != 'pending':
        flash('Cannot set ETA for a resolved order.', 'error')
        return redirect(url_for('merch.admin_sales'))

    eta_raw = (request.form.get('delivery_eta') or '').strip()
    if not eta_raw:
        order.delivery_eta = None
        db.session.commit()
        flash('Delivery ETA cleared', 'success')
        return redirect(url_for('merch.admin_sales'))

    try:
        eta_value = datetime.fromisoformat(eta_raw)
    except ValueError:
        flash('Invalid ETA format', 'error')
        return redirect(url_for('merch.admin_sales'))

    now = utc_now()
    purchased_at = order.purchased_at or now
    eta_deadline = purchased_at + timedelta(days=ETA_SET_DEADLINE_DAYS)
    if now > eta_deadline:
        deadline_str = eta_deadline.strftime('%Y-%m-%d %H:%M')
        flash(f'ETA can only be set within {ETA_SET_DEADLINE_DAYS} days of purchase (deadline {deadline_str}).', 'error')
        return redirect(url_for('merch.admin_sales'))

    if eta_value <= now:
        flash('ETA must be in the future.', 'error')
        return redirect(url_for('merch.admin_sales'))

    if eta_value > now + timedelta(days=ETA_MAX_DAYS):
        flash(f'ETA must be within {ETA_MAX_DAYS} days from now.', 'error')
        return redirect(url_for('merch.admin_sales'))

    order.delivery_eta = eta_value
    db.session.commit()
    flash('Delivery ETA updated', 'success')
    return redirect(url_for('merch.admin_sales'))


@merch_bp.route('/admin/sales')
@login_required
def admin_sales():
    """Admin / seller: View sales history."""
    if not (current_user.is_admin() or current_user.is_seller):
        flash('Admin access required', 'error')
        return redirect(url_for('merch.index'))

    now = utc_now()
    if current_user.is_seller and not current_user.is_admin():
        current_user.seller_sales_seen_at = now
        db.session.commit()
        cache.delete(f'profile_index_{current_user.id}')

    overdue_physical_query = MerchOrder.query.join(Product, Product.id == MerchOrder.product_id)\
        .filter(MerchOrder.product_type == 'physical')\
        .filter(MerchOrder.status == 'pending')\
        .filter(MerchOrder.delivery_eta.is_(None))
    if not current_user.is_admin():
        overdue_physical_query = overdue_physical_query.filter(Product.seller_id == current_user.id)
    did_auto_cancel = False
    for pending_order in overdue_physical_query.all():
        if auto_cancel_overdue_physical_order(
            pending_order,
            now,
            eta_set_deadline_days=ETA_SET_DEADLINE_DAYS,
        ):
            did_auto_cancel = True
    if did_auto_cancel:
        db.session.commit()

    page = request.args.get('page', 1, type=int)
    filter_type = (request.args.get('type') or '').strip().lower()
    per_page = 50

    query = db.session.query(MerchOrder, Product, User)\
        .join(Product, Product.id == MerchOrder.product_id)\
        .join(User, User.id == MerchOrder.user_id)

    if not current_user.is_admin():
        query = query.filter(Product.seller_id == current_user.id)
    if filter_type in {'digital', 'physical'}:
        query = query.filter(Product.product_type == filter_type)

    orders = query.order_by(MerchOrder.purchased_at.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)

    digital_rows = []
    physical_rows = []
    for order, product, buyer in orders.items:
        seller = product.seller
        fee_rate = float(seller.seller_commission_rate or 0) if seller else 0.0
        fee_rate = max(0.0, min(fee_rate, 1.0))
        fee_amount = order.total_price * fee_rate
        order_type = (order.product_type or product.product_type or 'digital').lower()

        if order_type == 'physical':
            purchased_at = order.purchased_at or now
            eta_deadline = purchased_at + timedelta(days=ETA_SET_DEADLINE_DAYS)
            payout = 0
            if order.status == 'delivered':
                payout = order.total_price - fee_amount
            elif (
                order.status == 'refunded'
                and order.delivery_eta
                and order.refunded_at
                and order.refunded_at < order.delivery_eta
            ):
                payout = _calculate_cancel_split(order.total_price)[1]
            physical_rows.append({
                'order': order,
                'product': product,
                'buyer': buyer,
                'seller': seller,
                'fee_rate': fee_rate,
                'fee_rate_percent': int(round(fee_rate * 100)),
                'fee_amount': fee_amount,
                'payout': payout,
                'eta_deadline': eta_deadline,
                'eta_deadline_passed': now > eta_deadline,
                'eta_min': now,
                'eta_max': now + timedelta(days=ETA_MAX_DAYS),
            })
        else:
            if order.status != 'completed':
                continue
            payout = order.total_price - fee_amount
            digital_rows.append({
                'order': order,
                'product': product,
                'buyer': buyer,
                'seller': seller,
                'fee_rate': fee_rate,
                'fee_rate_percent': int(round(fee_rate * 100)),
                'fee_amount': fee_amount,
                'payout': payout
            })

    return render_template(
        'merch/admin_sales.html',
        digital_sales=digital_rows,
        physical_sales=physical_rows,
        orders=orders
    )

register_marketplace_chat_routes(merch_bp)
