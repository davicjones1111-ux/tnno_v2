from app import create_app
from app.models import UserMission
app = create_app('development')
with app.app_context():
    subs = UserMission.query.all()
    for s in subs:
        print(s.id, repr(s.mission_photo))
