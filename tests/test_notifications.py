from datetime import datetime

from app.models import SellerNotification, UserNotification
from tests.test_support import AppTestCase


class NotificationTests(AppTestCase):
    def test_profile_notifications_marks_user_notifications_read(self):
        user = self.create_user(username='notify_user', password='abcdef')
        db_user_notification = UserNotification(user_id=user.id, message='Hello there')
        db_seller_notification = SellerNotification(
            seller_id=user.id,
            notification_type='new_message',
            title='Ping',
            message='New message',
            related_id=1,
            related_type='conversation',
            is_read=False,
        )
        from app.extensions import db
        db.session.add(db_user_notification)
        db.session.add(db_seller_notification)
        db.session.commit()

        self.login(username='notify_user', password='abcdef')
        response = self.client.get('/profile/notifications', follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        from app.extensions import db
        refreshed_user = db.session.get(UserNotification, db_user_notification.id)
        refreshed_seller = db.session.get(SellerNotification, db_seller_notification.id)
        self.assertIsNotNone(refreshed_user.read_at)
        self.assertTrue(refreshed_seller.is_read)
