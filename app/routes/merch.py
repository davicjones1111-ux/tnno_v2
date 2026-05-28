"""
Merch Store Routes
Digital product store with file delivery
"""
import os
import math
import secrets
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app.extensions import db, cache
from app.models import Product, ProductFile, ProductImage, ProductRating, ProductReaction, ProductReview, MerchOrder, User, SellerRating, SellerReport, UploadSession, UploadPart
from app.models import SellerChatConversation, SellerChatMessage, SellerNotification
from app.datetime_utils import utc_now
from app.services.seller_service import SELLER_PLANS
from app.services.history_service import HistoryService
from app.services.pagination_service import PaginationService
from app.services.wallet_service import WalletService
from app.services.object_storage_service import ObjectStorageService
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
MAX_PRODUCT_IMAGE_BYTES = 9 * 1024 * 1024
PRODUCT_UPLOAD_SMALL_FILE_THRESHOLD = 64 * 1024 * 1024
PRODUCT_UPLOAD_PART_SIZE = 16 * 1024 * 1024
PRODUCT_UPLOAD_SESSION_TTL_MINUTES = 120

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


def _product_image_slot_uploads():
    """Return product image uploads in slot order, with legacy multi-upload fallback."""
    slot_uploads = [request.files.get(f'image_{index}') for index in range(1, MAX_PRODUCT_IMAGES + 1)]
    if any(image and image.filename for image in slot_uploads) or any(
        f'image_{index}' in request.files for index in range(1, MAX_PRODUCT_IMAGES + 1)
    ):
        return slot_uploads

    legacy_uploads = _uploaded_product_images(request.files.getlist('images'))[:MAX_PRODUCT_IMAGES]
    return legacy_uploads + [None] * (MAX_PRODUCT_IMAGES - len(legacy_uploads))


def _save_product_gallery_image(uploaded_image, subfolder='merch'):
    """Save one uploaded product image and return the stored filename."""
    if not uploaded_image or not uploaded_image.filename:
        return None
    image_path = save_uploaded_image_optimized(
        uploaded_image,
        subfolder,
        max_bytes=MAX_PRODUCT_IMAGE_BYTES,
    )
    return image_path.split('/')[-1] if image_path else None


def _normalize_contact_link(value):
    """Accept bare domains like t.me/name by prefixing https:// before validation."""
    raw = (value or '').strip()
    if raw and '://' not in raw and '.' in raw and ' ' not in raw:
        return f'https://{raw}'
    return raw


def _normalize_seller_search(value):
    """Normalize seller search input like @username to username."""
    return (value or '').strip().lstrip('@').strip()


def _normalize_folder_path(value):
    """Normalize logical folder paths for product files."""
    raw = (value or '').replace('\\', '/').strip().strip('/')
    if not raw:
        return ''
    parts = [part for part in raw.split('/') if part and part not in {'.', '..'}]
    return '/'.join(parts[:10])


def _product_upload_settings():
    """Return upload thresholds from config with safe defaults."""
    return {
        'small_file_threshold': int(current_app.config.get('MERCH_UPLOAD_SMALL_FILE_THRESHOLD_BYTES') or PRODUCT_UPLOAD_SMALL_FILE_THRESHOLD),
        'part_size': int(current_app.config.get('MERCH_UPLOAD_PART_SIZE_BYTES') or PRODUCT_UPLOAD_PART_SIZE),
        'session_ttl_minutes': int(current_app.config.get('MERCH_UPLOAD_SESSION_TTL_MINUTES') or PRODUCT_UPLOAD_SESSION_TTL_MINUTES),
        'signed_url_expires': int(current_app.config.get('OBJECT_STORAGE_SIGNED_URL_EXPIRES_SECONDS') or 600),
        'batch_size': int(current_app.config.get('MERCH_UPLOAD_BATCH_SIZE') or 100),
    }


def _product_storage_key(product_id, file_id, original_name):
    ext = ''
    if original_name and '.' in original_name:
        ext = original_name.rsplit('.', 1)[1].lower()
    suffix = f'.{ext}' if ext else ''
    return f'products/{product_id}/files/{file_id}/{uuid.uuid4().hex}{suffix}'


def _multipart_part_count(file_size, part_size):
    return max(1, math.ceil(max(int(file_size or 0), 1) / max(int(part_size or 1), 1)))


def _file_upload_mode(file_size):
    settings = _product_upload_settings()
    return 'single' if int(file_size or 0) <= settings['small_file_threshold'] else 'multipart'


def _product_file_payload(product_file):
    return {
        'id': product_file.id,
        'product_id': product_file.product_id,
        'file_name': product_file.file_name or product_file.original_name or product_file.file_filename,
        'original_name': product_file.original_name,
        'file_type': product_file.file_type,
        'mime_type': product_file.mime_type,
        'file_size': int(product_file.file_size or 0),
        'storage_key': product_file.storage_key,
        'storage_url': product_file.storage_url,
        'folder_path': product_file.folder_path or '',
        'upload_status': product_file.upload_status,
        'checksum': product_file.checksum,
        'multipart_upload_id': product_file.multipart_upload_id,
        'part_count': int(product_file.part_count or 0),
        'upload_mode': _file_upload_mode(product_file.file_size),
    }


def _json_response(payload, status_code=200):
    response = jsonify(payload)
    response.status_code = status_code
    return response


def _require_storage():
    if not ObjectStorageService.enabled():
        return False, _json_response({
            'ok': False,
            'message': 'Object storage is not configured. Set S3/R2 credentials before uploading digital products.',
            'status': 'storage_not_configured'
        }, 503)
    return True, None


def _can_manage_product(product):
    if not product:
        return False
    if current_user.is_admin():
        return True
    return product.seller_id == current_user.id


def _save_product_gallery_images(uploaded_images, subfolder='merch'):
    """Save up to three uploaded product images and return stored filenames."""
    saved_filenames = []
    try:
        for image in uploaded_images:
            if not image or not image.filename:
                continue
            image_filename = _save_product_gallery_image(image, subfolder)
            if image_filename and image_filename not in saved_filenames:
                saved_filenames.append(image_filename)
            if len(saved_filenames) >= MAX_PRODUCT_IMAGES:
                break
    except ValueError:
        for filename in saved_filenames:
            delete_merch_file(filename, subfolder)
        raise
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
    ProductImage.query.filter_by(product_id=product.id).delete(synchronize_session=False)
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
    seller_search = _normalize_seller_search(request.args.get('seller', ''))
    product_type = (request.args.get('type') or '').strip().lower()
    sort = request.args.get('sort', 'latest').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 12
    
    seller_active_filter = or_(
        Product.seller_id.is_(None),
        User.role == 'admin',
        and_(
            User.is_seller.is_(True),
            or_(User.seller_expires_at.is_(None), User.seller_expires_at >= utc_now())
        )
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
    seller_search = _normalize_seller_search(request.args.get('seller', ''))
    product_type = (request.args.get('type') or '').strip().lower()
    sort = request.args.get('sort', 'latest').strip()
    
    # Enforce limits
    page = max(1, page)
    
    seller_active_filter = or_(
        Product.seller_id.is_(None),
        User.role == 'admin',
        and_(
            User.is_seller.is_(True),
            or_(User.seller_expires_at.is_(None), User.seller_expires_at >= utc_now())
        )
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
        product_type = (request.form.get('product_type') or 'digital').strip().lower()

        if product_type not in {'digital', 'physical'}:
            flash('Invalid product type', 'error')
            return redirect(url_for('merch.admin_create'))

        if product_type == 'physical':
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            price = request.form.get('price', type=int, default=0)
            contact_link = (request.form.get('contact_link') or '').strip()
            physical_quantity = request.form.get('physical_quantity', type=int, default=0)

            if not name:
                flash('Product name is required', 'error')
                return redirect(url_for('merch.admin_create'))

            if price < 1:
                flash('Price must be at least 1 TNNO', 'error')
                return redirect(url_for('merch.admin_create'))

            if physical_quantity < 1:
                flash('Physical quantity must be at least 1', 'error')
                return redirect(url_for('merch.admin_create'))
            contact_link = _normalize_contact_link(contact_link)
            if not contact_link:
                flash('Contact link is required for physical products', 'error')
                return redirect(url_for('merch.admin_create'))
            try:
                contact_link = validate_external_url(contact_link, field_name='Contact link')
            except ValidationError as exc:
                flash(str(exc), 'error')
                return redirect(url_for('merch.admin_create'))
        
        try:
            if product_type == 'physical':
                product = Product(
                    name=name,
                    description=description,
                    price=price,
                    product_type=product_type,
                    contact_link=contact_link if product_type == 'physical' else None,
                    physical_quantity=physical_quantity if product_type == 'physical' else 0,
                    seller_id=current_user.id if not current_user.is_admin() else None,
                    is_active=True,
                )
                db.session.add(product)
                db.session.commit()
                flash('Physical product created successfully!', 'success')
                return redirect(url_for('merch.admin_products'))

            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            price = request.form.get('price', type=int, default=0)
            digital_file = request.files.get('digital_file') or request.files.get('digital_files_1')
            if not digital_file or not digital_file.filename:
                flash('Digital file is required.', 'error')
                return redirect(url_for('merch.admin_create'))

            if not name:
                if digital_file and digital_file.filename:
                    base_name = digital_file.filename.rsplit('/', 1)[-1]
                    name = base_name.rsplit('.', 1)[0] if '.' in base_name else base_name
            if not name:
                flash('Product name is required.', 'error')
                return redirect(url_for('merch.admin_create'))
            if price < 1:
                flash('Price must be at least 1 TNNO', 'error')
                return redirect(url_for('merch.admin_create'))

            product, _ = _create_digital_product_bundle(
                name=name,
                description=description,
                price=price,
                uploaded_files=[digital_file],
                seller_id=current_user.id if not current_user.is_admin() else None,
            )
            db.session.commit()
            flash('Digital product created successfully!', 'success')
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


@merch_bp.route('/admin/products/<int:product_id>/files')
@login_required
def manage_product_files(product_id):
    """Manage direct-to-storage files for a digital product."""
    product = Product.query.get_or_404(product_id)
    if not _can_manage_product(product):
        flash('You do not have access to this product.', 'error')
        return redirect(url_for('merch.index'))
    if product.product_type != 'digital':
        flash('File manager is only available for digital products.', 'error')
        return redirect(url_for('merch.admin_edit', product_id=product.id))

    uploads = UploadSession.query.filter_by(product_id=product.id)\
        .order_by(UploadSession.created_at.desc())\
        .all()
    files = ProductFile.query.filter_by(product_id=product.id)\
        .order_by(ProductFile.created_at.desc())\
        .all()
    settings = _product_upload_settings()
    publish_state = _product_publish_state(product)
    return render_template(
        'merch/admin_product_files.html',
        product=product,
        files=files,
        uploads=uploads,
        upload_settings=settings,
        publish_state=publish_state,
    )


@merch_bp.route('/api/admin/products/<int:product_id>/upload-sessions', methods=['POST'])
@login_required
def create_product_upload_session(product_id):
    """Create a new upload session and file manifest for a product."""
    allowed, response = _require_storage()
    if not allowed:
        return response

    product = Product.query.get_or_404(product_id)
    if not _can_manage_product(product):
        return _json_response({'ok': False, 'message': 'Forbidden'}, 403)
    if product.product_type != 'digital':
        return _json_response({'ok': False, 'message': 'Upload sessions are only for digital products.'}, 400)

    payload = request.get_json(silent=True) or {}
    manifest = payload.get('files') or []
    if not isinstance(manifest, list) or not manifest:
        return _json_response({'ok': False, 'message': 'At least one file is required.'}, 400)

    settings = _product_upload_settings()
    now = utc_now()
    expires_at = now + timedelta(minutes=settings['session_ttl_minutes'])
    total_files = 0
    total_bytes = 0
    session = UploadSession(
        product_id=product.id,
        seller_id=product.seller_id or current_user.id,
        total_files=len(manifest),
        uploaded_files=0,
        total_bytes=0,
        status='active',
        expires_at=expires_at,
    )
    db.session.add(session)
    db.session.flush()

    created_files = []
    for item in manifest:
        if not isinstance(item, dict):
            continue
        original_name = secure_filename(item.get('name') or item.get('original_name') or '') or f'file-{session.id}'
        file_size = max(int(item.get('size') or 0), 0)
        if file_size > int(current_app.config.get('MERCH_UPLOAD_MAX_FILE_SIZE_BYTES') or (5 * 1024 * 1024 * 1024)):
            db.session.rollback()
            return _json_response({'ok': False, 'message': f'{original_name} exceeds the maximum upload size.'}, 400)

        folder_path = _normalize_folder_path(item.get('folder_path') or item.get('path') or '')
        mime_type = (item.get('type') or item.get('mime_type') or '').strip() or None
        file_ext = Path(original_name).suffix.lstrip('.').lower() or None
        product_file = ProductFile(
            product_id=product.id,
            upload_session_id=session.id,
            file_filename=original_name,
            original_name=original_name,
            file_name=original_name,
            file_type=file_ext,
            mime_type=mime_type,
            file_size=file_size,
            storage_provider=(current_app.config.get('OBJECT_STORAGE_PROVIDER') or 's3').lower(),
            folder_path=folder_path,
            upload_status='pending',
        )
        db.session.add(product_file)
        db.session.flush()

        product_file.storage_key = _product_storage_key(product.id, product_file.id, original_name)
        product_file.storage_url = ObjectStorageService.public_url(product_file.storage_key)
        total_files += 1
        total_bytes += file_size
        created_files.append(_product_file_payload(product_file))

    session.total_files = total_files
    session.total_bytes = total_bytes
    db.session.commit()

    return _json_response({
        'ok': True,
        'message': 'Upload session created.',
        'status': 'ok',
        'data': {
            'session': {
                'id': session.id,
                'product_id': session.product_id,
                'seller_id': session.seller_id,
                'total_files': session.total_files,
                'uploaded_files': session.uploaded_files,
                'total_bytes': int(session.total_bytes or 0),
                'status': session.status,
                'expires_at': session.expires_at.isoformat() if session.expires_at else None,
            },
            'files': created_files,
            'upload_settings': settings,
        }
    }, 201)


@merch_bp.route('/api/admin/product-files/<int:file_id>/presign', methods=['POST'])
@login_required
def presign_product_file(file_id):
    """Return a presigned URL or multipart part URL for a product file."""
    allowed, response = _require_storage()
    if not allowed:
        return response

    product_file = ProductFile.query.get_or_404(file_id)
    product = product_file.product
    if not _can_manage_product(product):
        return _json_response({'ok': False, 'message': 'Forbidden'}, 403)
    if product.product_type != 'digital':
        return _json_response({'ok': False, 'message': 'Digital files only.'}, 400)

    settings = _product_upload_settings()
    content_type = product_file.mime_type or 'application/octet-stream'
    upload_mode = _file_upload_mode(product_file.file_size)

    if upload_mode == 'single':
        product_file.upload_status = 'uploading'
        db.session.commit()
        return _json_response({
            'ok': True,
            'message': 'Presigned URL ready.',
            'status': 'ok',
            'data': {
                'file_id': product_file.id,
                'upload_mode': 'single',
                'storage_key': product_file.storage_key,
                'url': ObjectStorageService.generate_put_url(
                    key=product_file.storage_key,
                    content_type=content_type,
                    expires_in=settings['signed_url_expires'],
                ),
                'expires_in': settings['signed_url_expires'],
            }
        })

    if not product_file.multipart_upload_id:
        product_file.multipart_upload_id = ObjectStorageService.create_multipart_upload(
            key=product_file.storage_key,
            content_type=content_type,
        )
        product_file.part_count = _multipart_part_count(product_file.file_size, settings['part_size'])
        product_file.upload_status = 'multipart_uploading'
        db.session.commit()

    part_number = request.json.get('part_number') if request.is_json else request.form.get('part_number', type=int)
    if part_number is None:
        return _json_response({
            'ok': True,
            'message': 'Multipart session initialized.',
            'status': 'ok',
            'data': {
                'file_id': product_file.id,
                'upload_mode': 'multipart',
                'upload_id': product_file.multipart_upload_id,
                'storage_key': product_file.storage_key,
                'part_size': settings['part_size'],
                'part_count': int(product_file.part_count or 0),
            }
        })

    return _json_response({
        'ok': True,
        'message': 'Multipart part URL ready.',
        'status': 'ok',
        'data': {
            'file_id': product_file.id,
            'upload_mode': 'multipart',
            'upload_id': product_file.multipart_upload_id,
            'part_number': int(part_number),
            'url': ObjectStorageService.generate_part_url(
                key=product_file.storage_key,
                upload_id=product_file.multipart_upload_id,
                part_number=int(part_number),
                expires_in=settings['signed_url_expires'],
            ),
            'expires_in': settings['signed_url_expires'],
        }
    })


def _finalize_ready_product(product):
    if not product or product.product_type != 'digital':
        return
    pending = ProductFile.query.filter(
        ProductFile.product_id == product.id,
        ProductFile.upload_status.notin_({'ready', 'completed'})
    ).count()
    return pending == 0


def _product_publish_state(product):
    ready_files = ProductFile.query.filter_by(product_id=product.id, upload_status='ready').count()
    pending_files = ProductFile.query.filter(
        ProductFile.product_id == product.id,
        ProductFile.upload_status.notin_({'ready', 'completed'})
    ).count()
    return {
        'ready_files': int(ready_files or 0),
        'pending_files': int(pending_files or 0),
        'can_publish': bool(product.product_type == 'digital' and ready_files > 0 and pending_files == 0),
    }


def _create_digital_product_bundle(*, name, description, price, uploaded_files, seller_id):
    """Create one digital product from one file or folder upload."""
    product = Product(
        name=name,
        description=description,
        price=price,
        product_type='digital',
        seller_id=seller_id,
        is_active=True,
    )
    db.session.add(product)
    db.session.flush()

    cover_image_filename = None
    saved_files = []
    saved_images = []
    try:
        for uploaded_file in uploaded_files:
            if not uploaded_file or not uploaded_file.filename:
                continue
            if not allowed_file(uploaded_file.filename):
                raise ValueError(f'File type not allowed: {uploaded_file.filename}')

            safe_name = secure_filename(uploaded_file.filename)
            stored_filename = save_merch_file(uploaded_file, 'merch')
            if not stored_filename:
                raise ValueError(f'Unable to save file: {uploaded_file.filename}')

            saved_files.append(stored_filename)
            product_file = ProductFile(
                product_id=product.id,
                file_filename=stored_filename,
                original_name=safe_name,
                file_name=safe_name,
                file_type=(safe_name.rsplit('.', 1)[-1].lower() if '.' in safe_name else None),
                upload_status='ready',
            )
            db.session.add(product_file)

            ext = (uploaded_file.filename.rsplit('.', 1)[-1] or '').lower() if '.' in uploaded_file.filename else ''
            if ext in {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif', 'jfif', 'tiff', 'tif'}:
                if not cover_image_filename:
                    cover_image_filename = stored_filename
                    product.image_filename = cover_image_filename
                else:
                    saved_images.append(stored_filename)

        if not saved_files:
            raise ValueError('At least one valid file is required.')

        for index, image_filename in enumerate(saved_images, start=1):
            db.session.add(ProductImage(
                product_id=product.id,
                image_filename=image_filename,
                sort_order=index
            ))

        return product, saved_files
    except Exception:
        for filename in saved_files + saved_images:
            delete_merch_file(filename, 'merch')
        raise


@merch_bp.route('/api/admin/product-files/<int:file_id>/complete', methods=['POST'])
@login_required
def complete_product_file_upload(file_id):
    """Finalize one uploaded file after the storage upload finishes."""
    allowed, response = _require_storage()
    if not allowed:
        return response

    product_file = ProductFile.query.get_or_404(file_id)
    product = product_file.product
    if not _can_manage_product(product):
        return _json_response({'ok': False, 'message': 'Forbidden'}, 403)
    if product.product_type != 'digital':
        return _json_response({'ok': False, 'message': 'Digital files only.'}, 400)

    payload = request.get_json(silent=True) or {}
    uploaded_parts = payload.get('parts') or []
    checksum = (payload.get('checksum') or '').strip() or None
    storage_url = ObjectStorageService.public_url(product_file.storage_key)

    if product_file.multipart_upload_id:
        completed_parts = []
        for part in uploaded_parts:
            if not isinstance(part, dict):
                continue
            part_number = int(part.get('partNumber') or part.get('part_number') or 0)
            etag = (part.get('etag') or '').strip()
            if not part_number or not etag:
                continue
            completed_parts.append({'PartNumber': part_number, 'ETag': etag})
            upload_part = UploadPart.query.filter_by(file_id=product_file.id, part_number=part_number).first()
            if not upload_part:
                upload_part = UploadPart(session_id=product_file.upload_session_id, file_id=product_file.id, part_number=part_number)
                db.session.add(upload_part)
            upload_part.etag = etag
            upload_part.status = 'uploaded'
        if not completed_parts:
            return _json_response({'ok': False, 'message': 'At least one uploaded part is required.'}, 400)
        try:
            ObjectStorageService.complete_multipart_upload(
                key=product_file.storage_key,
                upload_id=product_file.multipart_upload_id,
                parts=completed_parts,
            )
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception('Multipart completion failed')
            return _json_response({'ok': False, 'message': f'Unable to finalize multipart upload: {exc}'}, 400)
    else:
        try:
            ObjectStorageService.head_object(key=product_file.storage_key)
        except Exception as exc:
            current_app.logger.warning('Head object check failed for %s: %s', product_file.storage_key, exc)

    product_file.upload_status = 'ready'
    product_file.storage_url = storage_url
    product_file.checksum = checksum
    product_file.part_count = product_file.part_count or len(uploaded_parts)
    db.session.commit()

    session = product_file.upload_session
    if session:
        session.uploaded_files = ProductFile.query.filter_by(upload_session_id=session.id, upload_status='ready').count()
        session.status = 'completed' if session.uploaded_files >= session.total_files else 'active'
        db.session.commit()

    _finalize_ready_product(product)
    db.session.commit()

    return _json_response({
        'ok': True,
        'message': 'File upload completed.',
        'status': 'ok',
        'data': {
            'file': _product_file_payload(product_file),
            'product_active': bool(product.is_active),
        }
    })


@merch_bp.route('/api/admin/upload-sessions/<int:session_id>/complete', methods=['POST'])
@login_required
def complete_upload_session(session_id):
    """Finalize an upload session after every file finishes."""
    session = UploadSession.query.get_or_404(session_id)
    product = session.product
    if not _can_manage_product(product):
        return _json_response({'ok': False, 'message': 'Forbidden'}, 403)

    ready_files = ProductFile.query.filter_by(upload_session_id=session.id, upload_status='ready').count()
    session.uploaded_files = ready_files
    session.status = 'completed' if ready_files >= session.total_files else 'active'
    _finalize_ready_product(product)
    db.session.commit()

    return _json_response({
        'ok': True,
        'message': 'Upload session updated.',
        'status': 'ok',
        'data': {
            'session_id': session.id,
            'uploaded_files': session.uploaded_files,
            'total_files': session.total_files,
            'status': session.status,
            'product_active': bool(product.is_active),
        }
    })


@merch_bp.route('/api/admin/upload-sessions/<int:session_id>/status')
@login_required
def upload_session_status(session_id):
    """Return upload session and file progress."""
    session = UploadSession.query.get_or_404(session_id)
    product = session.product
    if not _can_manage_product(product):
        return _json_response({'ok': False, 'message': 'Forbidden'}, 403)

    files = ProductFile.query.filter_by(upload_session_id=session.id)\
        .order_by(ProductFile.created_at.asc())\
        .all()
    return _json_response({
        'ok': True,
        'message': 'Upload session loaded.',
        'status': 'ok',
        'data': {
            'session': {
                'id': session.id,
                'product_id': session.product_id,
                'seller_id': session.seller_id,
                'total_files': session.total_files,
                'uploaded_files': session.uploaded_files,
                'total_bytes': int(session.total_bytes or 0),
                'status': session.status,
                'expires_at': session.expires_at.isoformat() if session.expires_at else None,
            },
            'files': [_product_file_payload(product_file) for product_file in files],
        }
    })


@merch_bp.route('/api/admin/upload-sessions/<int:session_id>/abort', methods=['POST'])
@login_required
def abort_upload_session(session_id):
    """Abort a pending upload session and any multipart uploads."""
    allowed, response = _require_storage()
    if not allowed:
        return response

    session = UploadSession.query.get_or_404(session_id)
    product = session.product
    if not _can_manage_product(product):
        return _json_response({'ok': False, 'message': 'Forbidden'}, 403)

    for product_file in ProductFile.query.filter_by(upload_session_id=session.id).all():
        if product_file.multipart_upload_id:
            try:
                ObjectStorageService.abort_multipart_upload(
                    key=product_file.storage_key,
                    upload_id=product_file.multipart_upload_id,
                )
            except Exception as exc:
                current_app.logger.warning('Multipart abort failed for file %s: %s', product_file.id, exc)
        product_file.upload_status = 'aborted'

    session.status = 'aborted'
    db.session.commit()
    return _json_response({
        'ok': True,
        'message': 'Upload session aborted.',
        'status': 'ok',
        'data': {'session_id': session.id, 'status': session.status}
    })


@merch_bp.route('/api/admin/products/<int:product_id>/publish', methods=['POST'])
@login_required
def publish_product(product_id):
    """Publish a digital draft after every required file is ready."""
    product = Product.query.get_or_404(product_id)
    if not _can_manage_product(product):
        return _json_response({'ok': False, 'message': 'Forbidden'}, 403)
    if product.product_type != 'digital':
        return _json_response({'ok': False, 'message': 'Only digital products use the file publish gate.'}, 400)

    publish_state = _product_publish_state(product)
    if not publish_state['can_publish']:
        return _json_response({
            'ok': False,
            'message': 'All product files must be uploaded and ready before publishing.',
            'data': publish_state,
        }, 400)

    product.is_active = True
    db.session.commit()

    return _json_response({
        'ok': True,
        'message': 'Product published successfully.',
        'status': 'ok',
        'data': {
            'product_id': product.id,
            'is_active': bool(product.is_active),
            **publish_state,
        }
    })


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
            contact_link = _normalize_contact_link(request.form.get('contact_link'))
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
        image_slots = _product_image_slot_uploads()
        remove_flags = [
            request.form.get(f'remove_image_{index}') == 'on'
            for index in range(1, MAX_PRODUCT_IMAGES + 1)
        ]
        gallery_files_to_delete = []
        gallery_update_requested = any(image and image.filename for image in image_slots) or any(remove_flags)
        if gallery_update_requested:
            newly_saved_files = []
            try:
                existing_gallery = product.gallery_filenames
                saved_slot_filenames = []

                for uploaded_image in image_slots:
                    saved_filename = _save_product_gallery_image(uploaded_image, 'merch')
                    saved_slot_filenames.append(saved_filename)
                    if saved_filename:
                        newly_saved_files.append(saved_filename)

                updated_gallery = []
                for index in range(MAX_PRODUCT_IMAGES):
                    if remove_flags[index]:
                        continue

                    if saved_slot_filenames[index]:
                        updated_gallery.append(saved_slot_filenames[index])
                        continue

                    existing_filename = existing_gallery[index] if index < len(existing_gallery) else None
                    if existing_filename:
                        updated_gallery.append(existing_filename)

                if len(updated_gallery) < MIN_PRODUCT_IMAGES:
                    for filename in newly_saved_files:
                        delete_merch_file(filename, 'merch')
                    flash('Keep at least Photo 1 or another remaining product photo.', 'error')
                    return redirect(url_for('merch.admin_edit', product_id=product.id))

                gallery_files_to_delete = _sync_product_gallery(product, updated_gallery)
            except ValueError as exc:
                for filename in newly_saved_files:
                    delete_merch_file(filename, 'merch')
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
