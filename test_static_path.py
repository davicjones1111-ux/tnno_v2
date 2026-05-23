from app import create_app
from app.models import UserMission
from flask import url_for

app = create_app('development')
with app.app_context():
    sub = UserMission.query.first()
    if sub:
        static_path = app.jinja_env.filters['static_path']
        normalized = static_path(sub.mission_photo)
        print('original:', sub.mission_photo)
        print('normalized:', normalized)
        print('url:', url_for('static', filename=normalized))
    else:
        print('no submissions')
