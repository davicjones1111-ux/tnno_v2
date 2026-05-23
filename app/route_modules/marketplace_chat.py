"""
Marketplace chat and seller notification routes.
"""
from __future__ import annotations

from flask import jsonify, redirect, render_template, request, url_for, flash
from flask_login import current_user, login_required

from app.datetime_utils import utc_now
from app.extensions import cache, db
from app.models import SellerChatConversation, SellerChatMessage, SellerNotification, User
from app.services.pagination_service import PaginationService
from app.utils import save_uploaded_file_any, save_uploaded_image_optimized


def register_marketplace_chat_routes(merch_bp):
    chat_file_extensions = {
        'pdf', 'txt', 'rtf', 'doc', 'docx', 'xls', 'xlsx', 'csv',
        'ppt', 'pptx', 'zip', 'rar', '7z', 'mp3', 'wav', 'm4a',
        'mp4', 'mov', 'webm'
    }

    def is_ajax_request() -> bool:
        return (
            request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or 'application/json' in request.headers.get('Accept', '')
        )

    def typing_cache_key(conversation_id: int, user_id: int) -> str:
        return f'seller_chat_typing:{conversation_id}:{user_id}'

    def build_attachment_name(stored_path: str | None) -> str:
        if not stored_path:
            return ''
        clean_path = str(stored_path).split('?', 1)[0].rstrip('/')
        leaf = clean_path.rsplit('/', 1)[-1] if '/' in clean_path else clean_path
        return leaf or 'attachment'

    def serialize_message(message: SellerChatMessage) -> dict:
        return {
            'id': message.id,
            'sender_id': message.sender_id,
            'sender_name': message.sender.username if message.sender else '',
            'message_type': message.message_type,
            'content': message.content,
            'image_path': message.image_path,
            'attachment_name': build_attachment_name(message.image_path) if message.message_type == 'file' else '',
            'created_at': message.created_at.isoformat() if message.created_at else None,
            'is_read': message.is_read,
        }

    @merch_bp.route('/seller/<int:seller_id>/chat')
    @login_required
    def seller_chat(seller_id):
        """Open or continue chat with a seller."""
        if seller_id == current_user.id:
            flash('You cannot chat with yourself.', 'error')
            return redirect(url_for('merch.index'))

        seller = User.query.get_or_404(seller_id)
        if not seller.is_seller and not seller.is_admin():
            flash('Seller not found', 'error')
            return redirect(url_for('merch.index'))

        conversation = SellerChatConversation.query.filter_by(
            buyer_id=current_user.id,
            seller_id=seller_id
        ).first()

        if not conversation:
            conversation = SellerChatConversation(
                buyer_id=current_user.id,
                seller_id=seller_id
            )
            db.session.add(conversation)
            db.session.commit()

        return redirect(url_for('merch.chat_conversation', conversation_id=conversation.id, mode='buyer'))

    @merch_bp.route('/chat/<int:conversation_id>')
    @login_required
    def chat_conversation(conversation_id):
        """Open a chat conversation for either buyer or seller."""
        conversation = SellerChatConversation.query.get_or_404(conversation_id)

        if current_user.id not in {conversation.buyer_id, conversation.seller_id}:
            flash('Access denied', 'error')
            return redirect(url_for('merch.index'))

        if conversation.buyer_id == conversation.seller_id:
            flash('You cannot chat with yourself.', 'error')
            return redirect(url_for('merch.my_chats'))

        requested_mode = (request.args.get('mode') or '').strip().lower()
        default_mode = 'buyer' if current_user.id == conversation.buyer_id else 'seller'
        if requested_mode not in {'buyer', 'seller'}:
            requested_mode = default_mode

        if requested_mode == 'buyer' and current_user.id != conversation.buyer_id:
            requested_mode = default_mode
        if requested_mode == 'seller' and current_user.id != conversation.seller_id:
            requested_mode = default_mode

        other_user = conversation.seller if current_user.id == conversation.buyer_id else conversation.buyer
        params = PaginationService.get_page_args(request.args.get('page', 1, type=int), 20)
        paginated_messages = PaginationService.paginate(
            SellerChatMessage.query.filter_by(conversation_id=conversation.id)
            .order_by(SellerChatMessage.created_at.desc(), SellerChatMessage.id.desc()),
            page=params.page,
            per_page=params.per_page,
        )
        messages = list(reversed(paginated_messages.items))
        first_unread = SellerChatMessage.query.filter(
            SellerChatMessage.conversation_id == conversation.id,
            SellerChatMessage.sender_id != current_user.id,
            SellerChatMessage.is_read.is_(False)
        ).order_by(SellerChatMessage.created_at.asc(), SellerChatMessage.id.asc()).first()
        unread_marker_id = first_unread.id if first_unread else None

        SellerChatMessage.query.filter(
            SellerChatMessage.conversation_id == conversation.id,
            SellerChatMessage.sender_id != current_user.id,
            SellerChatMessage.is_read.is_(False)
        ).update({'is_read': True})

        SellerNotification.query.filter_by(
            seller_id=current_user.id,
            related_type='conversation',
            related_id=conversation.id,
            is_read=False
        ).update({'is_read': True})
        db.session.commit()
        cache.delete(f'profile_index_{current_user.id}')

        return render_template(
            'merch/chat.html',
            conversation=conversation,
            other_user=other_user,
            messages=messages,
            chat_mode=requested_mode,
            messages_page=paginated_messages,
            unread_marker_id=unread_marker_id
        )

    @merch_bp.route('/chat/<int:conversation_id>/send', methods=['POST'])
    @login_required
    def send_message(conversation_id):
        """Send a message in a chat conversation."""
        conversation = SellerChatConversation.query.get_or_404(conversation_id)

        if current_user.id not in {conversation.buyer_id, conversation.seller_id}:
            flash('Access denied', 'error')
            return redirect(url_for('merch.index'))

        if conversation.buyer_id == conversation.seller_id:
            flash('You cannot chat with yourself.', 'error')
            return redirect(url_for('merch.my_chats'))

        message_text = request.form.get('message', '').strip()
        image = request.files.get('image')
        attachment = request.files.get('attachment')

        if not message_text and not (image and image.filename) and not (attachment and attachment.filename):
            if is_ajax_request():
                return jsonify({'ok': False, 'error': 'Message, image, or file is required'}), 400
            flash('Message, image, or file is required', 'error')
            return redirect(url_for('merch.chat_conversation', conversation_id=conversation.id))

        image_path = None
        message_type = 'text'
        if image and image.filename:
            try:
                image_path = save_uploaded_image_optimized(image, 'chat')
                message_type = 'image'
            except ValueError as exc:
                if is_ajax_request():
                    return jsonify({'ok': False, 'error': str(exc)}), 400
                flash(str(exc), 'error')
                return redirect(url_for('merch.chat_conversation', conversation_id=conversation.id))
        elif attachment and attachment.filename:
            try:
                image_path = save_uploaded_file_any(attachment, 'chat', chat_file_extensions)
                if not image_path:
                    allowed_list = ', '.join(sorted(chat_file_extensions))
                    error_message = f'Please upload a supported file: {allowed_list}'
                    if is_ajax_request():
                        return jsonify({'ok': False, 'error': error_message}), 400
                    flash(error_message, 'error')
                    return redirect(url_for('merch.chat_conversation', conversation_id=conversation.id))
                message_type = 'file'
            except ValueError as exc:
                if is_ajax_request():
                    return jsonify({'ok': False, 'error': str(exc)}), 400
                flash(str(exc), 'error')
                return redirect(url_for('merch.chat_conversation', conversation_id=conversation.id))

        message = SellerChatMessage(
            conversation_id=conversation_id,
            sender_id=current_user.id,
            message_type=message_type,
            content=message_text or None,
            image_path=image_path
        )
        db.session.add(message)
        conversation.updated_at = utc_now()

        recipient_id = conversation.seller_id if current_user.id == conversation.buyer_id else conversation.buyer_id
        if recipient_id == current_user.id:
            flash('You cannot chat with yourself.', 'error')
            return redirect(url_for('merch.my_chats'))

        recipient = User.query.get(recipient_id)
        if recipient:
            db.session.add(SellerNotification(
                seller_id=recipient_id,
                notification_type='new_message',
                title='New Message',
                message=f'{current_user.username} sent you a message',
                related_id=conversation_id,
                related_type='conversation'
            ))

        db.session.commit()

        if is_ajax_request():
            return jsonify({
                'ok': True,
                'message': serialize_message(message)
            })

        return redirect(url_for('merch.chat_conversation', conversation_id=conversation.id))

    @merch_bp.route('/chat/<int:conversation_id>/messages')
    @login_required
    def get_messages(conversation_id):
        """Get messages for a conversation (AJAX)."""
        conversation = SellerChatConversation.query.get_or_404(conversation_id)
        if current_user.id not in {conversation.buyer_id, conversation.seller_id}:
            return jsonify({'error': 'Access denied'}), 403

        params = PaginationService.get_page_args(request.args.get('page', 1, type=int), 20)
        paginated_messages = PaginationService.paginate(
            SellerChatMessage.query.filter_by(conversation_id=conversation_id)
            .order_by(SellerChatMessage.created_at.desc(), SellerChatMessage.id.desc()),
            page=params.page,
            per_page=params.per_page,
        )
        messages = list(reversed(paginated_messages.items))
        first_unread = SellerChatMessage.query.filter(
            SellerChatMessage.conversation_id == conversation_id,
            SellerChatMessage.sender_id != current_user.id,
            SellerChatMessage.is_read.is_(False)
        ).order_by(SellerChatMessage.created_at.asc(), SellerChatMessage.id.asc()).first()
        unread_marker_id = first_unread.id if first_unread else None

        SellerChatMessage.query.filter(
            SellerChatMessage.conversation_id == conversation_id,
            SellerChatMessage.sender_id != current_user.id,
            SellerChatMessage.is_read.is_(False)
        ).update({'is_read': True})
        db.session.commit()
        cache.delete(f'profile_index_{current_user.id}')

        other_user_id = conversation.seller_id if current_user.id == conversation.buyer_id else conversation.buyer_id
        other_user_typing = bool(cache.get(typing_cache_key(conversation_id, other_user_id)))

        return jsonify({
            'page': paginated_messages.page,
            'pages': paginated_messages.pages,
            'has_next': paginated_messages.has_next,
            'other_user_typing': other_user_typing,
            'unread_marker_id': unread_marker_id,
            'messages': [serialize_message(m) for m in messages]
        })

    @merch_bp.route('/chat/<int:conversation_id>/typing', methods=['POST'])
    @login_required
    def typing_status(conversation_id):
        """Lightweight typing heartbeat for chat UX."""
        conversation = SellerChatConversation.query.get_or_404(conversation_id)
        if current_user.id not in {conversation.buyer_id, conversation.seller_id}:
            return jsonify({'ok': False, 'error': 'Access denied'}), 403

        is_typing = bool((request.get_json(silent=True) or {}).get('typing'))
        key = typing_cache_key(conversation_id, current_user.id)
        if is_typing:
            cache.set(key, True, timeout=8)
        else:
            cache.delete(key)
        return jsonify({'ok': True})

    @merch_bp.route('/my-chats')
    @login_required
    def my_chats():
        """List all conversations for current user."""
        active_mode = (request.args.get('mode') or '').strip().lower()
        if active_mode not in {'buyer', 'seller'}:
            active_mode = 'seller' if (current_user.is_seller or current_user.is_admin()) else 'buyer'

        buyer_convs = SellerChatConversation.query.filter_by(buyer_id=current_user.id)\
            .order_by(SellerChatConversation.updated_at.desc()).all()

        seller_convs = []
        if current_user.is_seller or current_user.is_admin():
            seller_convs = SellerChatConversation.query.filter_by(seller_id=current_user.id)\
                .order_by(SellerChatConversation.updated_at.desc()).all()

        return render_template(
            'merch/my_chats.html',
            buyer_conversations=buyer_convs,
            seller_conversations=seller_convs,
            active_mode=active_mode
        )

    @merch_bp.route('/notifications')
    @login_required
    def notifications():
        """List seller notifications."""
        if not current_user.is_seller and not current_user.is_admin():
            flash('Access denied', 'error')
            return redirect(url_for('merch.index'))

        notifications_list = SellerNotification.query.filter_by(seller_id=current_user.id)\
            .order_by(SellerNotification.created_at.desc()).limit(50).all()

        SellerNotification.query.filter_by(seller_id=current_user.id, is_read=False)\
            .update({'is_read': True})
        db.session.commit()
        cache.delete(f'profile_index_{current_user.id}')

        return render_template('merch/notifications.html', notifications=notifications_list)
