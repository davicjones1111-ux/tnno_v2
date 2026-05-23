# RetroQuest Platform

A professional production-level Flask application with pixel art UI, cryptocurrency deposits, missions system, social feed, and gaming platform.

## Features

- **User Authentication**: Secure signup/login with bcrypt password hashing
- **Production Security**: CSP, HSTS, CSRF enforcement, safe redirects, brute-force protection, and suspicious-request logging
- **Missions System**: Complete tasks and earn coins
- **Social Feed**: Post, like, and comment on community posts
- **Cryptocurrency Deposits**: USDT deposits via BNB Chain (BEP-20)
- **Blockchain Auto-checker**: Automatic verification of deposits
- **Work Requests & Service Orders**: Multiple income streams
- **Withdrawal System**: Convert coins to USDT
- **Game Center**: Emperor's Circle card game with leaderboard
- **Admin Panel**: Full system management
- **Redis Support**: Redis cache, shared game state, and server-side session support
- **Production Logging**: Rotating application, access, warning, error, and security logs
- **Pixel Art UI**: Retro game dashboard style

## Project Structure

```
project/
├── app/
│   ├── __init__.py          # Flask app factory
│   ├── config.py            # Configuration
│   ├── extensions.py        # Flask extensions
│   ├── models.py            # SQLAlchemy models
│   ├── utils.py             # Utility functions
│   ├── routes/              # Blueprints
│   │   ├── auth.py
│   │   ├── missions.py
│   │   ├── deposit.py
│   │   ├── feed.py
│   │   ├── admin.py
│   │   ├── profile.py
│   │   ├── work.py
│   │   ├── api.py
│   │   └── game.py
│   ├── services/            # Business logic
│   │   ├── mission_service.py
│   │   ├── deposit_service.py
│   │   └── user_service.py
│   ├── templates/           # Jinja2 templates
│   └── static/
│       └── css/
│           └── pixel-ui.css # Pixel art UI
├── migrations/              # Flask-Migrate
├── instance/               # SQLite database
├── run.py                  # Entry point
├── requirements.txt        # Dependencies
├── Dockerfile              # Docker config
├── gunicorn.conf.py        # Gunicorn config
├── migrate.py              # Data migration
└── .env.example           # Environment config
```

## Installation

### 1. Clone and Setup

```bash
# Navigate to project directory
cd /path/to/project

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration

```bash
# Copy environment file
cp .env.example .env

# Edit .env with your settings
# Important: Change SECRET_KEY and ADMIN_PASS
```

### 3. Database Setup

```bash
# Initialize database (creates SQLite file)
python run.py

# OR migrate from old database
python migrate.py system.db
```

### 4. Run Development Server

```bash
python run.py
```

Visit `http://localhost:5000` in your browser.

## Deployment

### Render

This repo now includes [render.yaml](/Users/davicjones1111/Downloads/A%200/render.yaml) for a production-style Render setup:

- `retroquest-web` runs Gunicorn
- `retroquest-db` provisions PostgreSQL

Recommended Render settings:

- Keep `FLASK_ENV=production`
- Keep `AUTO_CREATE_SCHEMA_ON_START=0`
- Set `TRUST_PROXY_HEADERS=1`
- Set Cloudinary and NowPayments env vars if you use uploads/deposits

Before first traffic, run migrations:

```bash
flask --app run.py db upgrade -d migrations
```

### Production Stack (Recommended for High Concurrency)

Use:
- PostgreSQL (not SQLite)
- Redis (cache + sessions + shared game state)
- Gunicorn web workers
- Nginx reverse proxy

This repo includes:
- `Dockerfile`
- `docker-compose.prod.yml`
- `deploy/nginx.conf`

Start the stack:

```bash
cp .env.example .env
# Edit .env before continuing

docker compose -f docker-compose.prod.yml up -d --build
```

Provide TLS certificates for Nginx:

```bash
mkdir -p deploy/certs
# Place fullchain.pem and privkey.pem into deploy/certs/
```

Run database migrations once:

```bash
docker compose -f docker-compose.prod.yml run --rm web flask --app run.py db upgrade -d migrations
```

Start the web app and proxy:

```bash
docker compose -f docker-compose.prod.yml up -d web nginx
```

Important:
- Set strong secrets (`SECRET_KEY`, DB password).
- Run with `FLASK_ENV=production`.
- Set `DATABASE_URL`, `REDIS_URL`, `SESSION_REDIS_URL`, and `CACHE_REDIS_URL` to non-default production values.
- Keep `AUTO_CREATE_SCHEMA_ON_START=0` in production.
- Enable trusted proxy handling behind Render or Nginx with `TRUST_PROXY_HEADERS=1`.
- Tune `WEB_CONCURRENCY` and `GUNICORN_WORKER_CONNECTIONS` for your VPS size.
- Review `deploy/nginx.conf` and set the correct `server_name` plus certificate paths before exposing the host publicly.

### Gunicorn Only (Single Host)

```bash
gunicorn --config gunicorn.conf.py run:app
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| FLASK_ENV | Environment | development |
| SECRET_KEY | Secret key for sessions | (change in production) |
| DATABASE_URL | Database connection string | sqlite:///instance/database.db |
| REDIS_URL | Redis connection string | redis://localhost:6379/0 |
| ENABLE_SERVER_SIDE_SESSIONS | Use Redis-backed Flask sessions | 0 |
| SESSION_REDIS_URL | Redis session DB | redis://localhost:6379/1 |
| GAME_STATE_BACKEND | Emperor's Circle state backend (`memory`/`redis`) | memory |
| AUTO_CREATE_SCHEMA_ON_START | Run `db.create_all()` on app boot | 1 |
| ADMIN_USER | Admin username | admin |
| ADMIN_PASS | Admin password | (change in production) |
| USDT_TO_POINTS | Conversion rate | 4000 |

## API Endpoints

- `GET /api/user` - Get current user
- `GET /api/missions` - List missions
- `POST /api/missions/<id>/submit` - Submit mission
- `GET /api/feed` - Get social feed
- `POST /api/feed` - Create post
- `GET /api/leaderboard` - Get coin leaderboard
- `POST /api/game/score` - Save game score

## Game Integration

The Emperor's Circle game uses Socket.io for real-time multiplayer. To enable:

1. Set up a separate Node.js server with Socket.io
2. Configure the game client to connect to the game server
3. Use the `/api/game/score` endpoint to save scores

## Security Features

- Bcrypt password hashing
- CSRF protection
- CSP, HSTS, and secure browser headers via Flask-Talisman
- Secure file uploads with extension allowlists, MIME/signature validation, random filenames, and executable blocking
- Session rotation on login/logout and safer remember-me cookies
- Brute-force throttling and suspicious-request detection
- Rotating application, access, warning, error, and security logs
- SQL injection prevention (via SQLAlchemy)
- Session security
- Input validation

## License

MIT License
# tnno_v2
# tnno_v2
# tnno_v2
# tnno_v2
# tnno_v2
