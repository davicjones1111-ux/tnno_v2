# GitHub Copilot workspace instructions

## Purpose
This repository is a Flask-based production-style application for a gamified crypto platform. It includes backend logic, Flask blueprints, Jinja2 templates, a pixel-art UI, blockchain deposit handling, an admin panel, and a separate Node.js game server.

## What to focus on
- Backend: `app/` is the main application package.
- Routes: `app/routes/` contains Flask blueprints for auth, feed, deposit, game, missions, profile, work, and admin.
- Services: `app/services/` implements business logic separated from route handlers.
- Data models: `app/models.py` and `app/game_state.py` define domain entities and game state.
- Configuration: `app/config.py`, `run.py`, and `gunicorn.conf.py` define runtime behavior.
- Deployment: `Procfile`, `deploy/nginx.conf`, `Dockerfile`, and `docker-compose.prod.yml` are relevant for production.
- Tests: root-level `test_deposit.py` and `test_static_path.py` show project test style.

## Key commands
- Install dependencies: `pip install -r requirements.txt`
- Run locally: `python run.py`
- Run production server: `gunicorn --config gunicorn.conf.py run:app`
- Initialize DB: `python run.py` or `python migrate.py system.db`

## Environment
- Uses `.env.example` for environment variable setup.
- Important runtime variables include `SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, `GAME_STATE_BACKEND`, `START_BLOCKCHAIN_CHECKER`, and `AUTO_CREATE_SCHEMA_ON_START`.
- Production should use PostgreSQL and Redis, not the default SQLite setup.

## Notes for Copilot
- Prefer editing backend `app/` files unless the user explicitly requests UI/template changes.
- Preserve existing Flask app structure and configuration patterns.
- Avoid introducing new frontend frameworks; templates are Jinja2 and static assets are plain CSS/JS.
- There is a separate Node.js game server under `game-server/`; do not modify it unless the request explicitly involves Socket.io or the multiplayer game backend.

## When asked for contributions
- If the user asks for bug fixes, look for route-service interactions first.
- If the user asks for feature additions, check for existing route/service patterns before adding new modules.
- For security or deployment changes, keep production guidance aligned with README and existing env flags.
