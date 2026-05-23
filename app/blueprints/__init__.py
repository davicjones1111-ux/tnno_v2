"""
Central blueprint registry grouped by domain for cleaner app setup.
"""
from __future__ import annotations


def get_blueprints():
    from app.routes.admin import admin_bp
    from app.routes.api import api_bp
    from app.routes.auth import auth_bp
    from app.routes.deposit import deposit_bp, nowpayments_bp
    from app.routes.feed import feed_bp
    from app.routes.game import game_bp
    from app.routes.history import history_bp
    from app.routes.merch import merch_bp
    from app.routes.missions import missions_bp
    from app.routes.profile import profile_bp
    from app.routes.work import work_bp

    return {
        "auth": [(auth_bp, None)],
        "feed": [(feed_bp, "/feed")],
        "wallet": [(deposit_bp, "/deposit"), (nowpayments_bp, None), (work_bp, "/work")],
        "marketplace": [(merch_bp, "/store")],
        "profile": [(profile_bp, "/profile")],
        "missions": [(missions_bp, "/missions")],
        "game": [(game_bp, "/game")],
        "admin": [(admin_bp, "/admin")],
        "history": [(history_bp, None)],
        "api": [(api_bp, "/api")],
    }
