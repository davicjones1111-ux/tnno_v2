"""
Marketplace purchase, order, and download routes.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.datetime_utils import utc_now
from app.extensions import db
from app.models import MerchOrder, Product, ProductFile, SellerNotification
from app.services.history_service import HistoryService
from app.services.wallet_service import WalletService
from app.utils import send_uploaded_file
from app.validators import ValidationError


def auto_cancel_overdue_physical_order(order: MerchOrder, now, *, eta_set_deadline_days: int) -> bool:
    """Auto-refund a pending physical order if no ETA was set in time."""
    if not order:
        return False

    order_type = (order.product_type or order.product.product_type or 'digital').lower()
    if order_type != 'physical' or order.status != 'pending' or order.delivery_eta:
        return False

    purchased_at = order.purchased_at or now
    eta_deadline = purchased_at + timedelta(days=eta_set_deadline_days)
    if now < eta_deadline:
        return False

    WalletService.credit_user(
        user_id=order.user_id,
        amount=order.total_price,
        transaction_type='merch_order_refund_full',
        reference_type='merch_order',
        reference_id=order.id,
        details='auto_no_eta',
    )
    if order.product:
        order.product.physical_quantity = int(order.product.physical_quantity or 0) + int(order.quantity or 0)
        if order.product.seller_id:
            db.session.add(SellerNotification(
                seller_id=order.product.seller_id,
                notification_type='order_auto_cancelled',
                title='Order Auto Cancelled',
                message=f'Physical order for {order.product.name} was auto-cancelled because ETA was not set within 3 days.',
                related_id=order.id,
                related_type='order'
            ))
    order.status = 'refunded'
    order.refunded_at = now
    return True


def register_marketplace_order_routes(merch_bp, *, seller_active_fn, attach_cancel_metadata_fn,
                                      calculate_cancel_split_fn, eta_set_deadline_days: int):
    @merch_bp.route('/buy/<int:product_id>', methods=['POST'])
    @login_required
    def buy_product(product_id):
        """Purchase product."""
        product = Product.query.get_or_404(product_id)
        if product.seller_id is not None and not seller_active_fn(product.seller):
            flash('This seller is inactive. Product is unavailable.', 'error')
            return redirect(url_for('merch.index'))

        product_type = (product.product_type or 'digital').lower()
        try:
            quantity = int(request.form.get('quantity', 1))
        except ValueError:
            flash('Invalid quantity', 'error')
            return redirect(url_for('merch.index'))

        if quantity < 1:
            flash('Quantity must be at least 1', 'error')
            return redirect(url_for('merch.index'))

        if quantity > product.quantity:
            flash(f'Not enough stock. Available: {product.quantity}', 'error')
            return redirect(url_for('merch.index'))

        total_price = product.price * quantity
        if current_user.coins < total_price:
            flash(f'Insufficient balance. Need {total_price} TNNO, you have {current_user.coins}', 'error')
            return redirect(url_for('merch.index'))

        if product_type == 'physical':
            shipping_name = (request.form.get('shipping_name') or '').strip()
            shipping_country = (request.form.get('shipping_country') or '').strip()
            shipping_city = (request.form.get('shipping_city') or '').strip()
            shipping_phone = (request.form.get('shipping_phone') or '').strip()
            shipping_lat = request.form.get('shipping_lat', type=float)
            shipping_lng = request.form.get('shipping_lng', type=float)
            shipping_location_text = (request.form.get('shipping_location_text') or '').strip()

            if not all([shipping_name, shipping_country, shipping_city, shipping_phone]):
                flash('Name, country, city, and phone are required for physical orders', 'error')
                return redirect(url_for('merch.product_detail', product_id=product.id))

            if not shipping_location_text and (shipping_lat is None or shipping_lng is None):
                flash('Please share your location or type your address before ordering', 'error')
                return redirect(url_for('merch.product_detail', product_id=product.id))

            if not shipping_location_text:
                shipping_location_text = f'{shipping_lat},{shipping_lng}'

            try:
                WalletService.debit_user(
                    user_id=current_user.id,
                    amount=total_price,
                    transaction_type='merch_physical_order_hold',
                    details=f'product:{product.id}',
                )
                product.physical_quantity = max(int(product.physical_quantity or 0) - quantity, 0)
                order = MerchOrder(
                    user_id=current_user.id,
                    product_id=product.id,
                    product_type='physical',
                    quantity=quantity,
                    total_price=total_price,
                    status='pending',
                    shipping_name=shipping_name,
                    shipping_country=shipping_country,
                    shipping_city=shipping_city,
                    shipping_phone=shipping_phone,
                    shipping_lat=shipping_lat,
                    shipping_lng=shipping_lng,
                    shipping_location_text=shipping_location_text
                )
                db.session.add(order)
                db.session.flush()
                WalletService.record_transaction(
                    user_id=current_user.id,
                    amount=0,
                    transaction_type='merch_physical_order_created',
                    status='pending',
                    reference_type='merch_order',
                    reference_id=order.id,
                    details=f'product:{product.id}',
                )
                if product.seller_id:
                    db.session.add(SellerNotification(
                        seller_id=product.seller_id,
                        notification_type='new_purchase',
                        title='New Physical Order',
                        message=f'{current_user.username} placed a physical order for {product.name}',
                        related_id=order.id,
                        related_type='order'
                    ))
                db.session.commit()
                flash('Physical order placed. Awaiting delivery confirmation.', 'success')
                return redirect(url_for('merch.my_orders'))
            except ValidationError:
                db.session.rollback()
                flash('Insufficient TNNO', 'error')
                return redirect(url_for('merch.product_detail', product_id=product.id))
            except Exception as exc:
                db.session.rollback()
                current_app.logger.exception('Physical merchandise order processing failed')
                flash('Unable to process the order right now. Please try again.', 'error')
                return redirect(url_for('merch.product_detail', product_id=product.id))

        available_files = ProductFile.query.filter_by(
            product_id=product_id,
            is_sold=False
        ).limit(quantity).all()
        if len(available_files) < quantity:
            flash('Some files are no longer available', 'error')
            return redirect(url_for('merch.index'))

        try:
            WalletService.debit_user(
                user_id=current_user.id,
                amount=total_price,
                transaction_type='merch_digital_purchase',
                details=f'product:{product_id}',
            )
            order = MerchOrder(
                user_id=current_user.id,
                product_id=product_id,
                product_type='digital',
                quantity=quantity,
                total_price=total_price,
                status='completed'
            )
            db.session.add(order)
            db.session.flush()

            for file in available_files:
                file.is_sold = True
                file.order_id = order.id
                file.sold_at = utc_now()

            if product.seller:
                fee_rate = float(product.seller.seller_commission_rate or 0)
                fee_rate = max(0.0, min(fee_rate, 1.0))
                payout = int(total_price * (1 - fee_rate))
                WalletService.credit_user(
                    user_id=product.seller_id,
                    amount=payout,
                    transaction_type='merch_sale_payout',
                    reference_type='merch_order',
                    reference_id=order.id,
                    details=f'product:{product_id}',
                )
                db.session.add(SellerNotification(
                    seller_id=product.seller_id,
                    notification_type='new_purchase',
                    title='New Purchase!',
                    message=f'{current_user.username} purchased {quantity}x {product.name} for {total_price} TNNO',
                    related_id=order.id,
                    related_type='order'
                ))

            db.session.commit()
            flash(f'Successfully purchased {quantity}x {product.name}! Your balance: {current_user.coins} TNNO', 'success')
            return redirect(url_for('merch.my_orders'))
        except ValidationError:
            db.session.rollback()
            flash('Insufficient TNNO', 'error')
            return redirect(url_for('merch.index'))
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception('Digital merchandise purchase failed')
            flash('Unable to process the purchase right now. Please try again.', 'error')
            return redirect(url_for('merch.index'))

    @merch_bp.route('/my-orders')
    @login_required
    def my_orders():
        """User's purchased orders."""
        HistoryService.archive_due_items(user_id=current_user.id)
        now = utc_now()
        did_auto_cancel = False
        pending_auto_cancel = MerchOrder.query.filter_by(
            user_id=current_user.id,
            product_type='physical',
            status='pending'
        ).filter(MerchOrder.delivery_eta.is_(None)).all()
        for pending_order in pending_auto_cancel:
            if auto_cancel_overdue_physical_order(
                pending_order,
                now,
                eta_set_deadline_days=eta_set_deadline_days,
            ):
                did_auto_cancel = True
        if did_auto_cancel:
            db.session.commit()

        filter_type = (request.args.get('type') or '').strip().lower()
        page = request.args.get('page', 1, type=int)
        orders_page = MerchOrder.query.filter_by(user_id=current_user.id)\
            .filter(MerchOrder.is_archived.is_(False))\
            .order_by(MerchOrder.purchased_at.desc())\
            .paginate(page=page, per_page=20, error_out=False)
        orders = orders_page.items
        digital_orders = []
        physical_orders = []
        for order in orders:
            order_type = (order.product_type or order.product.product_type or 'digital').lower()
            if filter_type and order_type != filter_type:
                continue
            if order_type == 'physical':
                attach_cancel_metadata_fn(order, now)
                physical_orders.append(order)
            else:
                digital_orders.append(order)
        return render_template(
            'merch/orders.html',
            orders=orders,
            orders_page=orders_page,
            digital_orders=digital_orders,
            physical_orders=physical_orders,
            now=now
        )

    @merch_bp.route('/orders/<int:order_id>/arrived', methods=['POST'])
    @login_required
    def confirm_physical_arrived(order_id):
        """Buyer confirms physical order arrived."""
        order = MerchOrder.query.get_or_404(order_id)
        if order.user_id != current_user.id:
            flash('Access denied', 'error')
            return redirect(url_for('merch.my_orders'))

        order_type = (order.product_type or order.product.product_type or 'digital').lower()
        if order_type != 'physical':
            flash('This order is not a physical order', 'error')
            return redirect(url_for('merch.my_orders'))

        if order.status != 'pending':
            flash('Order is already resolved', 'error')
            return redirect(url_for('merch.my_orders'))

        if not order.delivery_eta:
            flash('Seller must set delivery ETA before you can confirm arrival.', 'error')
            return redirect(url_for('merch.my_orders'))

        product = order.product
        seller = product.seller
        if seller:
            fee_rate = float(seller.seller_commission_rate or 0)
            fee_rate = max(0.0, min(fee_rate, 1.0))
            payout = int(order.total_price * (1 - fee_rate))
            WalletService.credit_user(
                user_id=seller.id,
                amount=payout,
                transaction_type='merch_physical_delivery_payout',
                reference_type='merch_order',
                reference_id=order.id,
                details=f'product:{product.id}',
            )

        order.status = 'delivered'
        order.delivered_at = utc_now()
        db.session.commit()
        flash('Marked as arrived. Seller paid.', 'success')
        return redirect(url_for('merch.my_orders'))

    @merch_bp.route('/orders/<int:order_id>/not-arrived', methods=['POST'])
    @login_required
    def report_physical_not_arrived(order_id):
        """Buyer reports physical order not arrived (refund)."""
        order = MerchOrder.query.get_or_404(order_id)
        if order.user_id != current_user.id:
            flash('Access denied', 'error')
            return redirect(url_for('merch.my_orders'))

        order_type = (order.product_type or order.product.product_type or 'digital').lower()
        if order_type != 'physical':
            flash('This order is not a physical order', 'error')
            return redirect(url_for('merch.my_orders'))

        if order.status != 'pending':
            flash('Order is already resolved', 'error')
            return redirect(url_for('merch.my_orders'))

        now = utc_now()
        purchased_at = order.purchased_at or now
        eta_deadline = purchased_at + timedelta(days=eta_set_deadline_days)
        product = order.product

        if order.delivery_eta:
            if now < order.delivery_eta:
                buyer_refund, seller_payout, fee_amount = calculate_cancel_split_fn(order.total_price)
                WalletService.credit_user(
                    user_id=current_user.id,
                    amount=buyer_refund,
                    transaction_type='merch_order_refund_partial',
                    reference_type='merch_order',
                    reference_id=order.id,
                    details='buyer_partial_refund',
                )
                if product and product.seller and seller_payout > 0:
                    WalletService.credit_user(
                        user_id=product.seller_id,
                        amount=seller_payout,
                        transaction_type='merch_order_partial_payout',
                        reference_type='merch_order',
                        reference_id=order.id,
                        details='seller_partial_payout',
                    )
                if product:
                    product.physical_quantity = int(product.physical_quantity or 0) + int(order.quantity or 0)
                order.status = 'refunded'
                order.refunded_at = now
                db.session.commit()
                flash(
                    f'Order cancelled with penalty. Refunded {buyer_refund} TNNO, seller received {seller_payout} TNNO, fee {fee_amount} TNNO.',
                    'warning'
                )
                return redirect(url_for('merch.my_orders'))

            WalletService.credit_user(
                user_id=current_user.id,
                amount=order.total_price,
                transaction_type='merch_order_refund_full',
                reference_type='merch_order',
                reference_id=order.id,
                details='eta_passed',
            )
            if product:
                product.physical_quantity = int(product.physical_quantity or 0) + int(order.quantity or 0)
            order.status = 'refunded'
            order.refunded_at = now
            db.session.commit()
            flash('Order cancelled after ETA passed. Full refund issued.', 'success')
            return redirect(url_for('merch.my_orders'))

        if now < eta_deadline:
            deadline_str = eta_deadline.strftime('%Y-%m-%d %H:%M')
            flash(f'Seller has until {deadline_str} to set delivery time. Please wait.', 'error')
            return redirect(url_for('merch.my_orders'))

        WalletService.credit_user(
            user_id=current_user.id,
            amount=order.total_price,
            transaction_type='merch_order_refund_full',
            reference_type='merch_order',
            reference_id=order.id,
            details='no_eta',
        )
        if product:
            product.physical_quantity = int(product.physical_quantity or 0) + int(order.quantity or 0)
        order.status = 'refunded'
        order.refunded_at = now
        db.session.commit()

        flash('Seller did not set delivery time. Full refund issued.', 'success')
        return redirect(url_for('merch.my_orders'))

    @merch_bp.route('/download/<int:file_id>')
    @login_required
    def download_file(file_id):
        """Download purchased file."""
        product_file = ProductFile.query.get_or_404(file_id)
        if product_file.is_sold:
            order = MerchOrder.query.get(product_file.order_id)
            if not order or order.user_id != current_user.id:
                if not current_user.is_admin():
                    flash('You do not have permission to download this file', 'error')
                    return redirect(url_for('merch.my_orders'))
        else:
            if not current_user.is_admin():
                flash('File not purchased', 'error')
                return redirect(url_for('merch.index'))

        try:
            return send_uploaded_file(
                product_file.file_filename,
                subfolder='merch',
                download_name=product_file.original_name or 'file',
            )
        except (OSError, ValueError):
            current_app.logger.warning('Failed secure file download for product_file=%s', product_file.id)
            flash('File path is invalid', 'error')
            return redirect(url_for('merch.my_orders'))
