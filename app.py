import os
import re
import shutil
import secrets
import smtplib
import sqlite3
import zipfile
import datetime
import urllib.parse
import urllib.request
import json
from email.message import EmailMessage
from functools import wraps
from flask import (
    Flask,
    g,
    render_template,
    request,
    redirect,
    url_for,
    send_from_directory,
    abort,
    flash,
    session,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token
except Exception:
    google_requests = None
    id_token = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def resolve_data_dir(env=None, base_dir=BASE_DIR):
    env = env or os.environ
    return (
        env.get("ADAM_DATA_DIR")
        or env.get("RENDER_DATA_DIR")
        or os.path.join(base_dir, "data")
    )


DATA_DIR = resolve_data_dir()

DB_PATH = os.path.join(DATA_DIR, "projects.db")
PROJECTS_DIR = os.path.join(DATA_DIR, "projects")
BACKUPS_DIR = os.path.join(DATA_DIR, "backups")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(PROJECTS_DIR, exist_ok=True)
os.makedirs(BACKUPS_DIR, exist_ok=True)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.environ.get("SECRET_KEY", "dev-key-for-local-change-me")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB uploads
app.config["SITE_NAME"] = os.environ.get("SITE_NAME", "adamcoolsprojet.com")
app.config["PREFERRED_URL_SCHEME"] = os.environ.get("PREFERRED_URL_SCHEME", "https")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"
app.permanent_session_lifetime = datetime.timedelta(days=30)

ADMIN_EMAIL = "coolsadam00@gmail.com"
ADMIN_LOGIN = "ADMINADAM2155"
PREMADE_ACCOUNT_PASSWORD = "Brosky2155"
DEFAULT_AVATARS = (
    ("Blue Bolt", "/static/site-icon.svg"),
)
THUMBNAIL_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


@app.context_processor
def inject_site_name():
    return {
        "site_name": get_setting("site_name", app.config.get("SITE_NAME", "My Games")),
        "ui_accent": get_setting("accent_color", ""),
        "current_user": current_user(),
        "is_admin": is_admin(),
        "display_name": display_name(current_user()),
    }


def get_db():
    db = getattr(g, "db", None)
    if db is None:
        db = g.db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA synchronous = FULL")
    return db


def backup_database(reason="change"):
    if not os.path.exists(DB_PATH):
        return None
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    safe_reason = re.sub(r"[^a-z0-9_-]+", "-", reason.lower()).strip("-")[:40]
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S%f")
    filename = f"projects-{stamp}-{safe_reason or 'change'}.db"
    backup_path = os.path.join(BACKUPS_DIR, filename)
    temp_path = backup_path + ".tmp"
    source = sqlite3.connect(DB_PATH)
    target = sqlite3.connect(temp_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    os.replace(temp_path, backup_path)
    prune_database_backups()
    return backup_path


def prune_database_backups(keep=30):
    if not os.path.isdir(BACKUPS_DIR):
        return
    backups = sorted(
        name for name in os.listdir(BACKUPS_DIR) if name.endswith(".db")
    )
    for name in backups[:-keep]:
        try:
            os.remove(os.path.join(BACKUPS_DIR, name))
        except OSError:
            pass


def init_db():
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            description TEXT,
            tags TEXT,
            folder TEXT,
            created_at TEXT,
            thumbnail TEXT,
            platform_support TEXT NOT NULL DEFAULT 'pc',
            source_zip TEXT,
            source_token TEXT,
            runtime_language TEXT NOT NULL DEFAULT 'html',
            entry_file TEXT NOT NULL DEFAULT 'index.html'
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            username TEXT UNIQUE,
            name TEXT,
            picture TEXT,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT,
            last_login TEXT
        )
        """
    )
    user_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()
    }
    if "password_hash" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    if "email_verified" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
    if "verification_token" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN verification_token TEXT")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            score INTEGER NOT NULL,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(project_id, user_id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS avatar_options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            image_url TEXT UNIQUE NOT NULL,
            created_at TEXT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id INTEGER,
            details TEXT,
            created_at TEXT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS player_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            visitor_id TEXT,
            project_id INTEGER,
            action TEXT NOT NULL,
            details TEXT,
            ip_address TEXT,
            user_agent TEXT,
            created_at TEXT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS friendships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            friend_id INTEGER NOT NULL,
            requested_by INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(user_id, friend_id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            recipient_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_bans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            banned_by INTEGER,
            reason TEXT,
            created_at TEXT,
            expires_at TEXT,
            lifted_at TEXT,
            lifted_by INTEGER
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            created_at TEXT,
            UNIQUE(user_id, project_id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS security_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            visitor_id TEXT,
            project_id INTEGER,
            action TEXT NOT NULL,
            details TEXT,
            ip_address TEXT,
            user_agent TEXT,
            created_at TEXT
        )
        """
    )
    columns = {
        row["name"] for row in db.execute("PRAGMA table_info(projects)").fetchall()
    }
    if "thumbnail" not in columns:
        db.execute("ALTER TABLE projects ADD COLUMN thumbnail TEXT")
    if "platform_support" not in columns:
        db.execute("ALTER TABLE projects ADD COLUMN platform_support TEXT NOT NULL DEFAULT 'pc'")
    if "source_zip" not in columns:
        db.execute("ALTER TABLE projects ADD COLUMN source_zip TEXT")
    if "source_token" not in columns:
        db.execute("ALTER TABLE projects ADD COLUMN source_token TEXT")
    if "runtime_language" not in columns:
        db.execute("ALTER TABLE projects ADD COLUMN runtime_language TEXT NOT NULL DEFAULT 'html'")
    if "entry_file" not in columns:
        db.execute("ALTER TABLE projects ADD COLUMN entry_file TEXT NOT NULL DEFAULT 'index.html'")
    for row in db.execute("SELECT id FROM projects WHERE source_token IS NULL OR source_token = ''").fetchall():
        db.execute(
            "UPDATE projects SET source_token = ? WHERE id = ?",
            (secrets.token_urlsafe(24), row["id"]),
        )
    rebuild_users_if_email_is_required(db)
    user_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()
    }
    if "username" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN username TEXT")
        db.execute(
            "UPDATE users SET username = lower(substr(email, 1, instr(email, '@') - 1)) || id "
            "WHERE username IS NULL AND email IS NOT NULL AND instr(email, '@') > 1"
        )
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username)")
    seed_default_avatars(db)
    seed_premade_account(db)
    db.commit()


def rebuild_users_if_email_is_required(db):
    user_info = db.execute("PRAGMA table_info(users)").fetchall()
    if not any(row["name"] == "email" and row["notnull"] for row in user_info):
        return
    db.execute("ALTER TABLE users RENAME TO users_old")
    db.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            username TEXT UNIQUE,
            name TEXT,
            picture TEXT,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT,
            last_login TEXT,
            password_hash TEXT,
            email_verified INTEGER NOT NULL DEFAULT 0,
            verification_token TEXT
        )
        """
    )
    old_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(users_old)").fetchall()
    }
    username_expr = (
        "username" if "username" in old_columns
        else "lower(substr(email, 1, instr(email, '@') - 1)) || id"
    )
    db.execute(
        "INSERT INTO users "
        "(id, email, username, name, picture, is_admin, created_at, last_login, "
        "password_hash, email_verified, verification_token) "
        f"SELECT id, email, {username_expr}, name, picture, is_admin, created_at, "
        "last_login, password_hash, email_verified, verification_token FROM users_old"
    )
    db.execute("DROP TABLE users_old")


def seed_default_avatars(db):
    now = datetime.datetime.now(datetime.UTC).isoformat()
    for label, image_url in DEFAULT_AVATARS:
        db.execute(
            "INSERT OR IGNORE INTO avatar_options (label, image_url, created_at) "
            "VALUES (?, ?, ?)",
            (label, image_url, now),
        )


def seed_premade_account(db):
    now = datetime.datetime.now(datetime.UTC).isoformat()
    password_hash = generate_password_hash(PREMADE_ACCOUNT_PASSWORD)
    existing = db.execute(
        "SELECT id FROM users WHERE email = ?",
        (ADMIN_EMAIL,),
    ).fetchone()
    if existing:
        db.execute(
            "UPDATE users SET name = COALESCE(NULLIF(name, ''), ?), "
            "username = COALESCE(NULLIF(username, ''), ?), "
            "is_admin = 1, password_hash = ?, email_verified = 1, "
            "verification_token = NULL WHERE id = ?",
            (ADMIN_EMAIL.split("@")[0], ADMIN_LOGIN.lower(), password_hash, existing["id"]),
        )
        return
    db.execute(
        "INSERT INTO users (email, username, name, picture, is_admin, created_at, last_login, "
        "password_hash, email_verified, verification_token) "
        "VALUES (?, ?, ?, ?, 1, ?, ?, ?, 1, NULL)",
        (
            ADMIN_EMAIL,
            ADMIN_LOGIN.lower(),
            ADMIN_EMAIL.split("@")[0],
            "",
            now,
            now,
            password_hash,
        ),
    )


@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, "db", None)
    if db is not None:
        db.close()


@app.after_request
def backup_after_successful_change(response):
    skip_endpoints = {"project_heartbeat"}
    if (
        request.method == "POST"
        and response.status_code < 400
        and request.endpoint not in skip_endpoints
    ):
        try:
            backup_database(request.endpoint or "post")
        except Exception as error:
            app.logger.warning("Database backup failed: %s", error)
    return response


with app.app_context():
    init_db()
    close_db(None)


def safe_extract_zip(zipf: zipfile.ZipFile, target_dir: str):
    for name in zipf.namelist():
        parts = name.replace("\\", "/").split("/")
        if os.path.isabs(name) or ".." in parts:
            raise Exception("Unsafe file path in zip")
    zipf.extractall(target_dir)


def get_setting(key, default=None):
    try:
        row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return default
    return row["value"] if row else default


def set_setting(key, value):
    get_db().execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    get_db().commit()


def upsert_user(email, name="", picture=""):
    email = email.strip().lower()
    username = unique_username((name or email.split("@")[0]).strip() or email.split("@")[0])
    now = datetime.datetime.now(datetime.UTC).isoformat()
    is_seed_admin = 1 if email == ADMIN_EMAIL else 0
    db = get_db()
    existing = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        db.execute(
            "UPDATE users SET name = ?, username = COALESCE(NULLIF(username, ''), ?), "
            "picture = ?, last_login = ?, "
            "email_verified = 1, "
            "is_admin = CASE WHEN email = ? THEN 1 ELSE is_admin END WHERE id = ?",
            (name or existing["name"], username, picture or existing["picture"], now, ADMIN_EMAIL, existing["id"]),
        )
    else:
        db.execute(
            "INSERT INTO users (email, username, name, picture, is_admin, created_at, last_login, email_verified) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (email, username, name, picture, is_seed_admin, now, now, 1),
        )
    db.commit()
    return db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    if getattr(g, "current_user", None) is None:
        g.current_user = get_db().execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return g.current_user


def is_admin():
    user = current_user()
    return bool(user and user["is_admin"])


def is_owner(user=None):
    user = user or current_user()
    return bool(user and user["email"] == ADMIN_EMAIL)


def display_name(user):
    if not user:
        return ""
    return user["name"] or user["username"] or (user["email"].split("@")[0] if user["email"] else "player")


def user_label(user):
    if not user:
        return "Unknown"
    return user["name"] or user["username"] or user["email"] or f"User {user['id']}"


def friendship_pair(user_id, friend_id):
    return (min(user_id, friend_id), max(user_id, friend_id))


def create_friend_request(user_id, friend_id):
    if user_id == friend_id:
        raise ValueError("You cannot friend yourself.")
    left_id, right_id = friendship_pair(user_id, friend_id)
    now = datetime.datetime.now(datetime.UTC).isoformat()
    get_db().execute(
        "INSERT INTO friendships (user_id, friend_id, requested_by, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'pending', ?, ?) "
        "ON CONFLICT(user_id, friend_id) DO UPDATE SET "
        "requested_by = excluded.requested_by, "
        "status = CASE WHEN friendships.status = 'accepted' THEN 'accepted' ELSE 'pending' END, "
        "updated_at = excluded.updated_at",
        (left_id, right_id, user_id, now, now),
    )
    get_db().commit()


def accept_friend_request(user_id, requester_id):
    left_id, right_id = friendship_pair(user_id, requester_id)
    row = get_db().execute(
        "SELECT * FROM friendships WHERE user_id = ? AND friend_id = ?",
        (left_id, right_id),
    ).fetchone()
    if not row or row["requested_by"] == user_id:
        raise ValueError("No friend request from that user.")
    get_db().execute(
        "UPDATE friendships SET status = 'accepted', updated_at = ? "
        "WHERE user_id = ? AND friend_id = ?",
        (datetime.datetime.now(datetime.UTC).isoformat(), left_id, right_id),
    )
    get_db().commit()


def are_friends(user_id, friend_id):
    left_id, right_id = friendship_pair(user_id, friend_id)
    row = get_db().execute(
        "SELECT status FROM friendships WHERE user_id = ? AND friend_id = ?",
        (left_id, right_id),
    ).fetchone()
    return bool(row and row["status"] == "accepted")


BAD_WORDS = {
    "fuck",
    "shit",
    "bitch",
    "asshole",
    "nigger",
    "retard",
}

UNSAFE_MESSAGE_PATTERNS = (
    r"\bwhere\s+(do\s+)?(you|u)\s+live\b",
    r"\bwhat\s+is\s+your\s+address\b",
    r"\b(show|send)\s+(me\s+)?(your\s+)?(pic|pics|picture|pictures|photo|photos)\b",
    r"\bphone\s+number\b",
    r"\bmeet\s+me\b",
)


def validate_safe_message(body):
    text = " ".join(body.split())
    if not text:
        raise ValueError("Write a message first.")
    lowered = text.lower()
    words = set(re.findall(r"[a-z0-9']+", lowered))
    if words & BAD_WORDS:
        raise ValueError("That message has blocked words. Keep chat friendly.")
    if any(re.search(pattern, lowered) for pattern in UNSAFE_MESSAGE_PATTERNS):
        raise ValueError("Do not ask for addresses, live locations, photos, or private personal info.")
    return text[:1000]


def active_ban_for_user(user_id):
    now = datetime.datetime.now(datetime.UTC).isoformat()
    return get_db().execute(
        "SELECT * FROM user_bans WHERE user_id = ? AND lifted_at IS NULL "
        "AND (expires_at IS NULL OR expires_at > ?) ORDER BY id DESC LIMIT 1",
        (user_id, now),
    ).fetchone()


def current_ban():
    user = current_user()
    if not user:
        return None
    return active_ban_for_user(user["id"])


def ban_expiry(duration):
    if duration == "forever":
        return None
    days_by_duration = {
        "1h": 1 / 24,
        "1d": 1,
        "7d": 7,
        "30d": 30,
    }
    if duration not in days_by_duration:
        raise ValueError("Choose a valid ban duration.")
    expires = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=days_by_duration[duration])
    return expires.isoformat()


def log_admin_activity(action, target_type="", target_id=None, details=""):
    user = current_user()
    get_db().execute(
        "INSERT INTO admin_activity "
        "(user_id, action, target_type, target_id, details, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            user["id"] if user else None,
            action[:80],
            target_type[:80],
            target_id,
            details[:500],
            datetime.datetime.now(datetime.UTC).isoformat(),
        ),
    )


def log_security_alert(action, project_id=None, details=""):
    user = current_user()
    get_db().execute(
        "INSERT INTO security_alerts "
        "(user_id, visitor_id, project_id, action, details, ip_address, user_agent, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user["id"] if user else None,
            visitor_id(),
            project_id,
            action[:80],
            details[:500],
            request_ip(),
            request.headers.get("User-Agent", "")[:300],
            datetime.datetime.now(datetime.UTC).isoformat(),
        ),
    )


def visitor_id():
    if "visitor_id" not in session:
        session["visitor_id"] = secrets.token_urlsafe(16)
    return session["visitor_id"]


def request_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:80]
    return (request.remote_addr or "")[:80]


def log_player_activity(action, project_id=None, details=""):
    user = current_user()
    get_db().execute(
        "INSERT INTO player_activity "
        "(user_id, visitor_id, project_id, action, details, ip_address, user_agent, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user["id"] if user else None,
            visitor_id(),
            project_id,
            action[:80],
            details[:500],
            request_ip(),
            request.headers.get("User-Agent", "")[:300],
            datetime.datetime.now(datetime.UTC).isoformat(),
        ),
    )


def recent_player_activity(limit=40):
    return get_db().execute(
        "SELECT pa.*, u.name, u.username, u.email, p.title AS project_title "
        "FROM player_activity pa "
        "LEFT JOIN users u ON u.id = pa.user_id "
        "LEFT JOIN projects p ON p.id = pa.project_id "
        "ORDER BY pa.id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def recent_admin_activity(limit=20):
    return get_db().execute(
        "SELECT a.*, u.name, u.email FROM admin_activity a "
        "LEFT JOIN users u ON u.id = a.user_id "
        "ORDER BY a.id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def recent_security_alerts(limit=20):
    return get_db().execute(
        "SELECT s.*, u.name, u.username, u.email, p.title AS project_title "
        "FROM security_alerts s "
        "LEFT JOIN users u ON u.id = s.user_id "
        "LEFT JOIN projects p ON p.id = s.project_id "
        "ORDER BY s.id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not is_admin():
            abort(403)
        return view(*args, **kwargs)

    return wrapped


@app.before_request
def block_banned_users():
    allowed_endpoints = {"banned_notice", "logout", "static"}
    if request.endpoint in allowed_endpoints:
        return None
    if current_ban():
        return redirect(url_for("banned_notice"))
    return None


def safe_next_url(value):
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return url_for("index")


def clean_image_url(value):
    value = value.strip()
    if not value:
        return ""
    parsed = urllib.parse.urlparse(value)
    if value.startswith("/static/"):
        return value[:500]
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Image must be a full http/https URL or a /static/ path.")
    return value[:500]


def clean_username(value):
    username = value.strip().lower()
    if len(username) < 3 or len(username) > 32:
        raise ValueError("Username must be 3 to 32 characters.")
    if not re.fullmatch(r"[a-z0-9_.-]+", username):
        raise ValueError("Username can use letters, numbers, dots, dashes, and underscores.")
    return username


def unique_username(value):
    base = re.sub(r"[^a-z0-9_.-]+", "", value.strip().lower())[:24] or "player"
    db = get_db()
    username = base
    suffix = 2
    while db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
        username = f"{base}{suffix}"
        suffix += 1
    return username


def lookup_user_by_identifier(identifier):
    identifier = identifier.strip().lower()
    if identifier == ADMIN_LOGIN.lower():
        identifier = ADMIN_EMAIL
    if "@" in identifier:
        return get_db().execute("SELECT * FROM users WHERE email = ?", (identifier,)).fetchone()
    return get_db().execute("SELECT * FROM users WHERE username = ?", (identifier,)).fetchone()


def create_password_user(username, password, email=""):
    username = clean_username(username)
    email = email.strip().lower() or None
    token = secrets.token_urlsafe(32)
    now = datetime.datetime.now(datetime.UTC).isoformat()
    db = get_db()
    existing = db.execute(
        "SELECT * FROM users WHERE username = ? OR (email IS NOT NULL AND email = ?)",
        (username, email),
    ).fetchone()
    password_hash = generate_password_hash(password)
    is_seed_admin = 1 if email == ADMIN_EMAIL else 0
    if existing:
        if existing["username"] == username:
            raise ValueError("That username is already taken.")
        if email and existing["email"] == email:
            raise ValueError("That safety email is already used.")
        db.execute(
            "UPDATE users SET username = ?, password_hash = ?, verification_token = ?, "
            "email_verified = 0, is_admin = CASE WHEN email = ? THEN 1 ELSE is_admin END "
            "WHERE id = ?",
            (username, password_hash, token if email else None, ADMIN_EMAIL, existing["id"]),
        )
    else:
        db.execute(
            "INSERT INTO users (email, username, name, picture, is_admin, created_at, last_login, "
            "password_hash, email_verified, verification_token) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (email, username, username, "", is_seed_admin, now, now, password_hash, token if email else None),
        )
    db.commit()
    return db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def send_verification_email(email, token):
    verify_url = url_for("verify_email", token=token, _external=True)
    host = os.environ.get("SMTP_HOST")
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM", username or "no-reply@example.com")
    if not host or not username or not password:
        app.logger.warning("Email verification link for %s: %s", email, verify_url)
        return

    port = int(os.environ.get("SMTP_PORT", "587"))
    message = EmailMessage()
    message["Subject"] = f"Verify your {get_setting('site_name', app.config['SITE_NAME'])} account"
    message["From"] = sender
    message["To"] = email
    message.set_content(
        "Click this link to verify your account:\n\n"
        f"{verify_url}\n\n"
        "If you did not create this account, you can ignore this email."
    )
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(message)


def authenticate_password_user(identifier, password):
    db = get_db()
    identifier = identifier.strip().lower()
    if identifier in {ADMIN_LOGIN.lower(), ADMIN_EMAIL}:
        seed_premade_account(db)
        db.commit()
    user = lookup_user_by_identifier(identifier)
    if not user or not user["password_hash"]:
        return None, "Username/email or password is wrong."
    if not check_password_hash(user["password_hash"], password):
        return None, "Username/email or password is wrong."
    return user, None


def refresh_verification_token(user_id):
    token = secrets.token_urlsafe(32)
    get_db().execute(
        "UPDATE users SET verification_token = ? WHERE id = ?",
        (token, user_id),
    )
    get_db().commit()
    return token


def save_thumbnail(file, folder):
    if not file or not file.filename:
        return ""
    ext = os.path.splitext(file.filename.lower())[1]
    if ext not in THUMBNAIL_EXTENSIONS:
        raise ValueError("Thumbnail must be a PNG, JPG, GIF, or WebP image.")
    filename = f"thumbnail{ext}"
    file.save(os.path.join(folder, filename))
    return filename


PLATFORM_OPTIONS = {
    "mobile": "Mobile game",
    "pc": "PC game",
    "mobile_pc": "Mobile and PC supported",
}

LANGUAGE_OPTIONS = {
    "html": {
        "label": "HTML / index game",
        "extensions": {".html", ".htm", ".css", ".js", ".json", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp3", ".wav", ".ogg", ".wasm"},
        "default_entry": "index.html",
    },
    "python": {
        "label": "Python",
        "extensions": {".py", ".txt", ".md", ".json"},
        "default_entry": "main.py",
    },
    "java": {
        "label": "Java",
        "extensions": {".java", ".txt", ".md", ".json"},
        "default_entry": "Main.java",
    },
    "c": {
        "label": "C",
        "extensions": {".c", ".h", ".txt", ".md", ".json"},
        "default_entry": "main.c",
    },
    "cpp": {
        "label": "C++",
        "extensions": {".cpp", ".cc", ".cxx", ".hpp", ".h", ".txt", ".md", ".json"},
        "default_entry": "main.cpp",
    },
}


def validate_platform_support(value):
    value = value.strip()
    if value not in PLATFORM_OPTIONS:
        raise ValueError("Choose if this is a mobile game, PC game, or both.")
    return value


def validate_runtime_language(value):
    value = (value or "html").strip().lower()
    if value not in LANGUAGE_OPTIONS:
        raise ValueError("Choose HTML, Python, Java, C, or C++.")
    return value


def clean_entry_file(value, language):
    value = (value or LANGUAGE_OPTIONS[language]["default_entry"]).replace("\\", "/").strip()
    value = value.lstrip("/")
    if not value or ".." in value.split("/"):
        raise ValueError("Choose a safe entry file name.")
    normalized = os.path.normpath(value).replace("\\", "/")
    if normalized.startswith("../") or normalized == "..":
        raise ValueError("Choose a safe entry file name.")
    ext = os.path.splitext(normalized.lower())[1]
    if ext not in LANGUAGE_OPTIONS[language]["extensions"]:
        raise ValueError("The entry file extension does not match the selected language.")
    return normalized


def validate_project_files(folder, language, entry_file):
    entry_path = os.path.abspath(os.path.join(folder, entry_file))
    folder_abs = os.path.abspath(folder)
    if os.path.commonpath([entry_path, folder_abs]) != folder_abs or not os.path.exists(entry_path):
        raise ValueError(f"Your upload must include {entry_file}.")
    allowed = LANGUAGE_OPTIONS[language]["extensions"]
    for root, dirs, files in os.walk(folder):
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        for name in files:
            if name == "source.zip":
                continue
            ext = os.path.splitext(name.lower())[1]
            if ext and ext not in allowed:
                raise ValueError(f"{name} is not allowed for {LANGUAGE_OPTIONS[language]['label']} uploads.")


def save_and_extract_game_zip(file, folder, language="html", entry_file="index.html"):
    if not file:
        raise ValueError("Please choose a zip file containing your project (HTML/CSS/JS).")
    if not file.filename.lower().endswith(".zip"):
        raise ValueError("Only .zip uploads are accepted for now.")
    os.makedirs(folder, exist_ok=True)
    zip_path = os.path.join(folder, "source.zip")
    file.save(zip_path)
    with zipfile.ZipFile(zip_path, "r") as zf:
        safe_extract_zip(zf, folder)
    validate_project_files(folder, language, entry_file)
    return "source.zip"


def save_code_as_project_zip(code, folder, language, entry_file):
    code = code or ""
    if not code.strip():
        raise ValueError("Write code before creating the upload.")
    os.makedirs(folder, exist_ok=True)
    entry_path = os.path.abspath(os.path.join(folder, entry_file))
    folder_abs = os.path.abspath(folder)
    if os.path.commonpath([entry_path, folder_abs]) != folder_abs:
        raise ValueError("Choose a safe entry file name.")
    os.makedirs(os.path.dirname(entry_path), exist_ok=True)
    with open(entry_path, "w", encoding="utf-8", newline="\n") as source_file:
        source_file.write(code[:500000])
    zip_path = os.path.join(folder, "source.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(entry_path, entry_file)
    return "source.zip"


def apply_lulu_command(prompt):
    text = prompt.strip()
    lowered = text.lower()
    blocked = ("app.py", "server command", "run ", "execute", "shell", "python", "delete file")
    if any(word in lowered for word in blocked):
        return None, "That is not allowed. Lulu can only use safe admin actions."

    match = re.search(r"(?:change|set|rename)\s+(?:the\s+)?site\s+name\s+to\s+(.+)", text, re.I)
    if match:
        name = match.group(1).strip()[:80]
        set_setting("site_name", name)
        return f"Site name changed to {name}.", None

    match = re.search(r"(?:change|set)\s+(?:the\s+)?(?:accent|theme)\s+color\s+to\s+(#[0-9a-f]{6})", text, re.I)
    if match:
        color = match.group(1).lower()
        set_setting("accent_color", color)
        return f"Accent color changed to {color}.", None

    match = re.search(r"rename\s+game\s+(.+?)\s+to\s+(.+)", text, re.I)
    if match:
        old_title = match.group(1).strip()
        new_title = match.group(2).strip()[:100]
        cur = get_db().execute(
            "UPDATE projects SET title = ? WHERE lower(title) = lower(?)",
            (new_title, old_title),
        )
        get_db().commit()
        if cur.rowcount == 0:
            return None, "Lulu could not find that game."
        return f"Game renamed to {new_title}.", None

    match = re.search(r"make\s+(.+@\S+)\s+admin", text, re.I)
    if match:
        email = match.group(1).strip().lower()
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            return None, "Lulu could not find that user."
        get_db().execute("UPDATE users SET is_admin = 1 WHERE id = ?", (user["id"],))
        get_db().commit()
        return f"{email} is now an admin.", None

    return None, "Lulu did not understand that safe action yet."


@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    db = get_db()
    if q:
        like = f"%{q}%"
        rows = db.execute(
            "SELECT p.*, AVG(r.score) AS avg_rating, COUNT(r.id) AS rating_count "
            "FROM projects p LEFT JOIN ratings r ON r.project_id = p.id "
            "WHERE p.title LIKE ? OR p.description LIKE ? OR p.tags LIKE ? "
            "GROUP BY p.id ORDER BY p.id DESC",
            (like, like, like),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT p.*, AVG(r.score) AS avg_rating, COUNT(r.id) AS rating_count "
            "FROM projects p LEFT JOIN ratings r ON r.project_id = p.id "
            "GROUP BY p.id ORDER BY p.id DESC"
        ).fetchall()
    if q:
        users = db.execute(
            "SELECT id, name, username, email, picture, is_admin FROM users "
            "WHERE name LIKE ? OR username LIKE ? OR email LIKE ? "
            "ORDER BY name LIMIT 12",
            (like, like, like),
        ).fetchall()
    else:
        users = []
    projects = [dict(r) for r in rows]
    visible_q = q if is_admin() or "@" not in q else ""
    return render_template(
        "index.html",
        projects=projects,
        users=users,
        q=q,
        visible_q=visible_q,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user, error = authenticate_password_user(
            request.form.get("email", ""),
            request.form.get("password", ""),
        )
        if error:
            flash(error)
            return redirect(url_for("login"))
        session.clear()
        session.permanent = request.form.get("remember_device") == "on"
        session["user_id"] = user["id"]
        log_player_activity("login", details=f"Signed in as {display_name(user)}")
        get_db().commit()
        return redirect(safe_next_url(request.form.get("next")))

    return render_template(
        "login.html",
        google_ready=bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET")),
        next_url=safe_next_url(request.args.get("next")),
    )


@app.route("/signup", methods=["POST"])
def signup():
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")
    agree_terms = request.form.get("agree_terms")
    try:
        clean_username(username)
    except ValueError as error:
        flash(str(error))
        return redirect(url_for("login"))
    if email and "@" not in email:
        flash("Enter a valid safety email address, or leave it empty.")
        return redirect(url_for("login"))
    if len(password) < 8:
        flash("Password must be at least 8 characters.")
        return redirect(url_for("login"))
    if password != confirm_password:
        flash("Passwords do not match.")
        return redirect(url_for("login"))
    if agree_terms != "on":
        flash("You must agree to the Website Rules and Privacy Policy.")
        return redirect(url_for("login"))

    try:
        user = create_password_user(username, password, email)
    except ValueError as error:
        flash(str(error))
        return redirect(url_for("login"))
    if user["email"] and user["verification_token"]:
        send_verification_email(user["email"], user["verification_token"])
        flash("Account created. You can sign in now. Check your email to verify your safety address.")
    else:
        flash("Account created. You can sign in now.")
    return redirect(url_for("login"))


@app.route("/resend-verification", methods=["POST"])
def resend_verification():
    user = lookup_user_by_identifier(request.form.get("email", ""))
    if not user:
        flash("No account was found for that username or email.")
        return redirect(url_for("login"))
    if not user["email"]:
        flash("This account does not have a safety email yet.")
        return redirect(url_for("login"))
    if user["email_verified"]:
        flash("That email is already verified. You can sign in.")
        return redirect(url_for("login"))
    token = user["verification_token"] or refresh_verification_token(user["id"])
    send_verification_email(user["email"], token)
    flash("Verification email sent again. Check your inbox and spam folder.")
    return redirect(url_for("login"))


@app.route("/verify-email/<token>")
def verify_email(token):
    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE verification_token = ?",
        (token,),
    ).fetchone()
    if not user:
        flash("That verification link is invalid or expired.")
        return redirect(url_for("login"))
    db.execute(
        "UPDATE users SET email_verified = 1, verification_token = NULL WHERE id = ?",
        (user["id"],),
    )
    db.commit()
    flash("Email verified. You can sign in now.")
    return redirect(url_for("login"))


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/banned")
@login_required
def banned_notice():
    ban = current_ban()
    if not ban:
        return redirect(url_for("index"))
    return render_template("banned.html", ban=ban)


@app.route("/auth/google")
def google_login():
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    if not client_id:
        flash("Google sign-in is not configured yet.")
        return redirect(url_for("login"))
    state = os.urandom(16).hex()
    session["oauth_state"] = state
    session["oauth_next"] = safe_next_url(request.args.get("next"))
    params = {
        "client_id": client_id,
        "redirect_uri": url_for("google_callback", _external=True),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params))


@app.route("/auth/google/callback")
def google_callback():
    if request.args.get("state") != session.pop("oauth_state", None):
        abort(400)
    code = request.args.get("code")
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not code or not client_id or not client_secret:
        abort(400)
    if id_token is None or google_requests is None:
        flash("Install google-auth to use Google sign-in.")
        return redirect(url_for("login"))

    data = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": url_for("google_callback", _external=True),
            "grant_type": "authorization_code",
        }
    ).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        token_payload = json.loads(response.read().decode("utf-8"))
    identity = id_token.verify_oauth2_token(
        token_payload["id_token"],
        google_requests.Request(),
        client_id,
    )
    user = upsert_user(
        identity["email"],
        identity.get("name", ""),
        identity.get("picture", ""),
    )
    next_url = safe_next_url(session.pop("oauth_next", None))
    session.clear()
    session["user_id"] = user["id"]
    return redirect(next_url)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/account")
@login_required
def account():
    user = current_user()
    db = get_db()
    avatars = get_db().execute(
        "SELECT * FROM avatar_options ORDER BY id",
    ).fetchall()
    pending_requests = db.execute(
        "SELECT f.*, u.id AS other_id, u.name, u.username, u.email, u.picture "
        "FROM friendships f JOIN users u ON u.id = f.requested_by "
        "WHERE f.status = 'pending' AND f.requested_by != ? "
        "AND (f.user_id = ? OR f.friend_id = ?) ORDER BY f.id DESC",
        (user["id"], user["id"], user["id"]),
    ).fetchall()
    friends = db.execute(
        "SELECT u.* FROM friendships f JOIN users u "
        "ON u.id = CASE WHEN f.user_id = ? THEN f.friend_id ELSE f.user_id END "
        "WHERE f.status = 'accepted' AND (f.user_id = ? OR f.friend_id = ?) "
        "ORDER BY u.name, u.username",
        (user["id"], user["id"], user["id"]),
    ).fetchall()
    favorite_projects = db.execute(
        "SELECT p.* FROM favorites f JOIN projects p ON p.id = f.project_id "
        "WHERE f.user_id = ? ORDER BY f.id DESC",
        (user["id"],),
    ).fetchall()
    messages = db.execute(
        "SELECT m.*, s.name AS sender_name, s.username AS sender_username, "
        "s.email AS sender_email, r.name AS recipient_name, r.username AS recipient_username, "
        "r.email AS recipient_email "
        "FROM messages m "
        "JOIN users s ON s.id = m.sender_id "
        "JOIN users r ON r.id = m.recipient_id "
        "WHERE m.sender_id = ? OR m.recipient_id = ? "
        "ORDER BY m.id DESC LIMIT 20",
        (user["id"], user["id"]),
    ).fetchall()
    return render_template(
        "account.html",
        avatars=avatars,
        pending_requests=pending_requests,
        friends=friends,
        friend_points=len(friends) * 10,
        favorite_projects=favorite_projects,
        messages=messages,
    )


@app.route("/account/profile", methods=["POST"])
@login_required
def update_profile():
    username = " ".join(request.form.get("username", "").split())
    if len(username) < 2 or len(username) > 32:
        flash("Username must be 2 to 32 characters.")
        return redirect(url_for("account"))
    if not re.fullmatch(r"[A-Za-z0-9 _.-]+", username):
        flash("Username can use letters, numbers, spaces, dots, dashes, and underscores.")
        return redirect(url_for("account"))
    picture = current_user()["picture"] or ""
    avatar_id = request.form.get("avatar_id")
    if avatar_id is not None:
        if avatar_id == "":
            picture = ""
        else:
            avatar = get_db().execute(
                "SELECT image_url FROM avatar_options WHERE id = ?",
                (avatar_id,),
            ).fetchone()
            if not avatar:
                flash("Choose one of the profile pictures from the website.")
                return redirect(url_for("account"))
            picture = avatar["image_url"]
    get_db().execute(
        "UPDATE users SET name = ?, picture = ? WHERE id = ?",
        (username, picture, current_user()["id"]),
    )
    get_db().commit()
    g.current_user = None
    flash("Profile saved.")
    return redirect(url_for("account"))


@app.route("/account/delete", methods=["POST"])
@login_required
def delete_account():
    user = current_user()
    get_db().execute("DELETE FROM users WHERE id = ?", (user["id"],))
    get_db().commit()
    session.clear()
    flash("Your account was deleted.")
    return redirect(url_for("index"))


@app.route("/friends/<int:user_id>/request", methods=["POST"])
@login_required
def request_friend(user_id):
    target = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        abort(404)
    try:
        create_friend_request(current_user()["id"], user_id)
    except ValueError as error:
        flash(str(error))
        return redirect(url_for("account"))
    get_db().commit()
    flash(f"Friend request sent to {user_label(target)}.")
    return redirect(request.referrer or url_for("account"))


@app.route("/friends/<int:user_id>/accept", methods=["POST"])
@login_required
def accept_friend(user_id):
    requester = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not requester:
        abort(404)
    try:
        accept_friend_request(current_user()["id"], user_id)
    except ValueError as error:
        flash(str(error))
        return redirect(url_for("account"))
    get_db().commit()
    flash(f"You are now friends with {user_label(requester)}.")
    return redirect(url_for("account"))


@app.route("/messages/<int:user_id>", methods=["POST"])
@login_required
def send_message(user_id):
    recipient = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not recipient:
        abort(404)
    if not are_friends(current_user()["id"], user_id):
        abort(403)
    try:
        body = validate_safe_message(request.form.get("body", ""))
    except ValueError as error:
        flash(str(error))
        return redirect(url_for("account"))
    get_db().execute(
        "INSERT INTO messages (sender_id, recipient_id, body, created_at) "
        "VALUES (?, ?, ?, ?)",
        (
            current_user()["id"],
            user_id,
            body,
            datetime.datetime.now(datetime.UTC).isoformat(),
        ),
    )
    log_player_activity("send_friend_message", None, f"Sent message to {user_label(recipient)}")
    get_db().commit()
    flash("Message sent.")
    return redirect(url_for("account"))


@app.route("/upload", methods=["GET", "POST"])
@admin_required
def upload():
    if request.method == "POST":
        title = request.form.get("title", "Untitled").strip()
        description = request.form.get("description", "").strip()
        tags = request.form.get("tags", "").strip()
        file = request.files.get("file")
        try:
            platform_support = validate_platform_support(request.form.get("platform_support", ""))
            runtime_language = validate_runtime_language(request.form.get("runtime_language", "html"))
            entry_file = clean_entry_file("", runtime_language)
        except ValueError as error:
            flash(str(error))
            return redirect(request.url)
        if request.form.get("confirm_upload") != "on":
            flash("Confirm the game type and upload rules before uploading.")
            return redirect(request.url)

        db = get_db()
        created_at = datetime.datetime.now(datetime.UTC).isoformat()
        source_token = secrets.token_urlsafe(24)
        cur = db.execute(
            "INSERT INTO projects (title, description, tags, folder, created_at, thumbnail, "
            "platform_support, source_zip, source_token, runtime_language, entry_file) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                title,
                description,
                tags,
                "",
                created_at,
                "",
                platform_support,
                "",
                source_token,
                runtime_language,
                entry_file,
            ),
        )
        db.commit()
        pid = cur.lastrowid
        folder = os.path.join(PROJECTS_DIR, str(pid))

        try:
            source_zip = save_and_extract_game_zip(file, folder, runtime_language, entry_file)
            thumbnail = save_thumbnail(request.files.get("thumbnail"), folder)
        except Exception as e:
            if os.path.isdir(folder):
                shutil.rmtree(folder)
            db.execute("DELETE FROM projects WHERE id = ?", (pid,))
            db.commit()
            flash("Failed to upload zip: " + str(e))
            return redirect(request.url)

        db.execute(
            "UPDATE projects SET folder = ?, thumbnail = ?, source_zip = ? WHERE id = ?",
            (str(pid), thumbnail, source_zip, pid),
        )
        log_admin_activity(
            "upload_project",
            "project",
            pid,
            f"Uploaded {runtime_language} project: {title}",
        )
        db.commit()

        flash("Project uploaded successfully.")
        return redirect(url_for("index"))

    return render_template(
        "upload.html",
        platform_options=PLATFORM_OPTIONS,
        language_options=LANGUAGE_OPTIONS,
    )


@app.route("/project/<int:pid>")
def view_project(pid):
    db = get_db()
    row = db.execute(
        "SELECT p.*, AVG(r.score) AS avg_rating, COUNT(r.id) AS rating_count "
        "FROM projects p LEFT JOIN ratings r ON r.project_id = p.id "
        "WHERE p.id = ? GROUP BY p.id",
        (pid,),
    ).fetchone()
    if not row:
        abort(404)
    project = dict(row)
    user_rating = None
    is_favorite = False
    if current_user():
        rating = db.execute(
            "SELECT score FROM ratings WHERE project_id = ? AND user_id = ?",
            (pid, current_user()["id"]),
        ).fetchone()
        if rating:
            user_rating = rating["score"]
        is_favorite = bool(
            db.execute(
                "SELECT id FROM favorites WHERE project_id = ? AND user_id = ?",
                (pid, current_user()["id"]),
            ).fetchone()
        )
    comments = db.execute(
        "SELECT c.*, u.name, u.username, u.email, u.picture, u.is_admin "
        "FROM comments c JOIN users u ON u.id = c.user_id "
        "WHERE c.project_id = ? ORDER BY c.id DESC",
        (pid,),
    ).fetchall()

    # find index.html inside folder
    folder = os.path.join(PROJECTS_DIR, str(pid))
    index_candidates = [project.get("entry_file") or "index.html", "index.html", "index.htm", "game.html"]
    found = None
    if (project.get("runtime_language") or "html") == "html":
        for root, dirs, files in os.walk(folder):
            for cand in index_candidates:
                if cand in files:
                    rel = os.path.relpath(os.path.join(root, cand), folder)
                    found = rel.replace("\\", "/")
                    break
            if found:
                break
    log_player_activity(
        "open_game_page",
        pid,
        f"Opened game page: {project['title']}",
    )
    db.commit()

    return render_template(
        "view.html",
        project=project,
        index_file=found,
        user_rating=user_rating,
        is_favorite=is_favorite,
        comments=comments,
    )


@app.route("/project/<int:pid>/heartbeat", methods=["POST"])
def project_heartbeat(pid):
    db = get_db()
    project = db.execute("SELECT title FROM projects WHERE id = ?", (pid,)).fetchone()
    if not project:
        abort(404)
    log_player_activity(
        "active_play",
        pid,
        f"Playing {project['title']}",
    )
    db.commit()
    return ("", 204)


@app.route("/project/<int:pid>/rate", methods=["POST"])
@login_required
def rate_project(pid):
    db = get_db()
    project = db.execute("SELECT id FROM projects WHERE id = ?", (pid,)).fetchone()
    if not project:
        abort(404)
    try:
        score = int(request.form.get("score", ""))
    except ValueError:
        score = 0
    if score < 1 or score > 5:
        flash("Choose a rating from 1 to 5.")
        return redirect(url_for("view_project", pid=pid))
    now = datetime.datetime.now(datetime.UTC).isoformat()
    db.execute(
        "INSERT INTO ratings (project_id, user_id, score, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(project_id, user_id) DO UPDATE SET "
        "score = excluded.score, updated_at = excluded.updated_at",
        (pid, current_user()["id"], score, now, now),
    )
    log_player_activity("rate_game", pid, f"Rated game {score}/5")
    db.commit()
    flash("Rating saved.")
    return redirect(url_for("view_project", pid=pid))


@app.route("/project/<int:pid>/comments", methods=["POST"])
@login_required
def add_comment(pid):
    db = get_db()
    project = db.execute("SELECT id FROM projects WHERE id = ?", (pid,)).fetchone()
    if not project:
        abort(404)
    body = request.form.get("body", "").strip()
    if not body:
        flash("Write a comment before posting.")
        return redirect(url_for("view_project", pid=pid))
    body = body[:1000]
    db.execute(
        "INSERT INTO comments (project_id, user_id, body, created_at) "
        "VALUES (?, ?, ?, ?)",
        (
            pid,
            current_user()["id"],
            body,
            datetime.datetime.now(datetime.UTC).isoformat(),
        ),
    )
    log_player_activity("comment_game", pid, "Posted a comment")
    db.commit()
    flash("Comment posted.")
    return redirect(url_for("view_project", pid=pid))


@app.route("/project/<int:pid>/favorite", methods=["POST"])
@login_required
def favorite_project(pid):
    db = get_db()
    project = db.execute("SELECT id, title FROM projects WHERE id = ?", (pid,)).fetchone()
    if not project:
        abort(404)
    user_id = current_user()["id"]
    favorite = db.execute(
        "SELECT id FROM favorites WHERE user_id = ? AND project_id = ?",
        (user_id, pid),
    ).fetchone()
    if favorite:
        db.execute("DELETE FROM favorites WHERE id = ?", (favorite["id"],))
        flash("Removed from favorites.")
    else:
        db.execute(
            "INSERT INTO favorites (user_id, project_id, created_at) VALUES (?, ?, ?)",
            (user_id, pid, datetime.datetime.now(datetime.UTC).isoformat()),
        )
        flash("Added to favorites.")
    log_player_activity("favorite_game", pid, f"Toggled favorite for {project['title']}")
    db.commit()
    return redirect(request.referrer or url_for("view_project", pid=pid))


@app.route("/comments/<int:comment_id>/delete", methods=["POST"])
@admin_required
def delete_comment(comment_id):
    db = get_db()
    comment = db.execute(
        "SELECT * FROM comments WHERE id = ?",
        (comment_id,),
    ).fetchone()
    if not comment:
        abort(404)
    project_id = comment["project_id"]
    project = db.execute(
        "SELECT title FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    db.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
    log_admin_activity(
        "delete_comment",
        "comment",
        comment_id,
        f"Deleted comment on {project['title'] if project else 'deleted game'}",
    )
    db.commit()
    flash("Comment deleted.")
    return redirect(url_for("view_project", pid=project_id))


@app.route("/project_files/<int:pid>/<path:filename>")
def project_files(pid, filename):
    db = get_db()
    project = db.execute("SELECT source_zip FROM projects WHERE id = ?", (pid,)).fetchone()
    if not project:
        abort(404)
    requested = filename.replace("\\", "/").split("/")[-1]
    if requested == (project["source_zip"] or "") or requested.lower() == "source.zip":
        log_security_alert(
            "Blocked public source ZIP request",
            pid,
            f"Attempted public file request: {filename}",
        )
        db.commit()
        abort(404)
    folder = os.path.join(PROJECTS_DIR, str(pid))
    full = os.path.join(folder, filename)
    if not os.path.commonpath([os.path.abspath(full), folder]) == os.path.abspath(folder):
        log_security_alert(
            "Blocked unsafe project file path",
            pid,
            f"Attempted unsafe file request: {filename}",
        )
        db.commit()
        abort(403)
    if not os.path.exists(full):
        abort(404)
    return send_from_directory(folder, filename)


@app.route("/admin/projects/<int:pid>/source.zip")
@admin_required
def download_project_source(pid):
    abort(404)


@app.route("/admin/projects/<int:pid>/source/<token>.zip")
@admin_required
def download_project_source_token(pid, token):
    project = get_db().execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    if not project or not project["source_zip"] or not secrets.compare_digest(token, project["source_token"] or ""):
        log_security_alert(
            "Blocked bad source ZIP token",
            pid,
            "Admin source ZIP download was requested with a bad token.",
        )
        get_db().commit()
        abort(404)
    folder = os.path.abspath(os.path.join(PROJECTS_DIR, str(pid)))
    source_path = os.path.join(folder, project["source_zip"])
    if not os.path.exists(source_path):
        abort(404)
    return send_from_directory(
        folder,
        project["source_zip"],
        as_attachment=True,
        download_name=f"{project['title'] or 'project'}-source.zip",
    )


@app.route("/admin/projects/<int:pid>/replace", methods=["POST"])
@admin_required
def replace_project_source(pid):
    db = get_db()
    project = db.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    if not project:
        abort(404)
    try:
        platform_support = validate_platform_support(request.form.get("platform_support", ""))
        runtime_language = validate_runtime_language(request.form.get("runtime_language", project["runtime_language"] or "html"))
        entry_file = clean_entry_file("", runtime_language)
    except ValueError as error:
        flash(str(error))
        return redirect(url_for("admin_panel"))
    if request.form.get("confirm_upload") != "on":
        flash("Confirm the replacement before updating this game.")
        return redirect(url_for("admin_panel"))

    folder = os.path.abspath(os.path.join(PROJECTS_DIR, str(pid)))
    projects_root = os.path.abspath(PROJECTS_DIR)
    if os.path.commonpath([folder, projects_root]) != projects_root:
        abort(403)
    backup_folder = folder + ".replace-backup"
    if os.path.exists(backup_folder):
        shutil.rmtree(backup_folder)
    if os.path.isdir(folder):
        shutil.copytree(folder, backup_folder)
        shutil.rmtree(folder)
    os.makedirs(folder, exist_ok=True)
    try:
        source_zip = save_and_extract_game_zip(request.files.get("file"), folder, runtime_language, entry_file)
    except Exception as error:
        if os.path.isdir(folder):
            shutil.rmtree(folder)
        if os.path.isdir(backup_folder):
            shutil.copytree(backup_folder, folder)
            shutil.rmtree(backup_folder)
        flash("Failed to replace zip: " + str(error))
        return redirect(url_for("admin_panel"))
    if os.path.isdir(backup_folder):
        shutil.rmtree(backup_folder)
    db.execute(
        "UPDATE projects SET platform_support = ?, source_zip = ?, runtime_language = ?, entry_file = ? WHERE id = ?",
        (platform_support, source_zip, runtime_language, entry_file, pid),
    )
    log_admin_activity(
        "replace_project_zip",
        "project",
        pid,
        f"Replaced uploaded files for: {project['title']}",
    )
    db.commit()
    flash("Game files updated.")
    return redirect(url_for("admin_panel"))


def project_file_list(pid):
    folder = os.path.join(PROJECTS_DIR, str(pid))
    if not os.path.isdir(folder):
        return []
    files = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        for name in names:
            if name == "source.zip":
                continue
            rel = os.path.relpath(os.path.join(root, name), folder).replace("\\", "/")
            files.append(rel)
    return sorted(files)


@app.route("/admin")
@admin_required
def admin_panel():
    q = request.args.get("q", "").strip()
    db = get_db()
    if q:
        like = f"%{q}%"
        projects = db.execute(
            "SELECT * FROM projects WHERE title LIKE ? OR description LIKE ? OR tags LIKE ? ORDER BY id DESC",
            (like, like, like),
        ).fetchall()
        users = db.execute(
            "SELECT * FROM users WHERE email LIKE ? OR name LIKE ? OR username LIKE ? ORDER BY last_login DESC",
            (like, like, like),
        ).fetchall()
    else:
        projects = db.execute("SELECT * FROM projects ORDER BY id DESC").fetchall()
        users = db.execute("SELECT * FROM users ORDER BY last_login DESC").fetchall()
    projects = [dict(project) for project in projects]
    for project in projects:
        project["files"] = project_file_list(project["id"])
    avatars = db.execute("SELECT * FROM avatar_options ORDER BY id DESC").fetchall()
    activities = recent_admin_activity()
    player_activities = recent_player_activity()
    bans = db.execute(
        "SELECT b.*, u.name, u.username, u.email, u.is_admin, admin.name AS banned_by_name, "
        "admin.username AS banned_by_username, admin.email AS banned_by_email "
        "FROM user_bans b JOIN users u ON u.id = b.user_id "
        "LEFT JOIN users admin ON admin.id = b.banned_by "
        "WHERE b.lifted_at IS NULL "
        "ORDER BY b.id DESC",
    ).fetchall()
    return render_template(
        "admin.html",
        projects=projects,
        users=users,
        avatars=avatars,
        activities=activities,
        player_activities=player_activities,
        security_alerts=recent_security_alerts(),
        bans=bans,
        q=q,
        lulu_message=None,
    )


@app.route("/admin/projects/<int:pid>/delete", methods=["POST"])
@admin_required
def delete_project(pid):
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    if not row:
        abort(404)
    folder = os.path.abspath(os.path.join(PROJECTS_DIR, str(pid)))
    projects_root = os.path.abspath(PROJECTS_DIR)
    if os.path.commonpath([folder, projects_root]) == projects_root and os.path.isdir(folder):
        shutil.rmtree(folder)
    db.execute("DELETE FROM projects WHERE id = ?", (pid,))
    log_admin_activity(
        "delete_project",
        "project",
        pid,
        f"Removed game: {row['title']}",
    )
    db.commit()
    flash("Game removed.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/users/<int:user_id>/make-admin", methods=["POST"])
@admin_required
def make_admin(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        abort(404)
    db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (user_id,))
    log_admin_activity(
        "make_admin",
        "user",
        user_id,
        f"Made admin: {user['email']}",
    )
    db.commit()
    flash("User is now an admin.")
    return redirect(url_for("admin_panel"))


def ensure_can_moderate_user(target):
    if target["email"] == ADMIN_EMAIL:
        abort(403)
    if target["is_admin"] and not is_owner():
        abort(403)


@app.route("/admin/users/<int:user_id>/remove-admin", methods=["POST"])
@admin_required
def remove_admin(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        abort(404)
    if not is_owner() or user["email"] == ADMIN_EMAIL:
        abort(403)
    db.execute("UPDATE users SET is_admin = 0 WHERE id = ?", (user_id,))
    log_admin_activity(
        "remove_admin",
        "user",
        user_id,
        f"Removed admin: {user_label(user)}",
    )
    db.commit()
    flash("Admin access removed.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/users/<int:user_id>/ban", methods=["POST"])
@admin_required
def ban_user(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        abort(404)
    ensure_can_moderate_user(user)
    try:
        expires_at = ban_expiry(request.form.get("duration", ""))
    except ValueError as error:
        flash(str(error))
        return redirect(url_for("admin_panel"))
    reason = " ".join(request.form.get("reason", "").split())[:300]
    db.execute(
        "INSERT INTO user_bans (user_id, banned_by, reason, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            user_id,
            current_user()["id"],
            reason,
            datetime.datetime.now(datetime.UTC).isoformat(),
            expires_at,
        ),
    )
    log_admin_activity(
        "ban_user",
        "user",
        user_id,
        f"Banned {user_label(user)}: {reason or 'No reason'}",
    )
    db.commit()
    flash("User banned.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/users/<int:user_id>/unban", methods=["POST"])
@admin_required
def unban_user(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        abort(404)
    ensure_can_moderate_user(user)
    db.execute(
        "UPDATE user_bans SET lifted_at = ?, lifted_by = ? "
        "WHERE user_id = ? AND lifted_at IS NULL",
        (datetime.datetime.now(datetime.UTC).isoformat(), current_user()["id"], user_id),
    )
    log_admin_activity(
        "unban_user",
        "user",
        user_id,
        f"Unbanned {user_label(user)}",
    )
    db.commit()
    flash("User unbanned.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/avatars", methods=["POST"])
@admin_required
def add_avatar():
    label = " ".join(request.form.get("label", "").split())[:80]
    try:
        image_url = clean_image_url(request.form.get("image_url", ""))
    except ValueError as error:
        flash(str(error))
        return redirect(url_for("admin_panel"))
    if not label:
        flash("Avatar needs a name.")
        return redirect(url_for("admin_panel"))
    if not image_url:
        flash("Avatar needs an image URL.")
        return redirect(url_for("admin_panel"))
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO avatar_options (label, image_url, created_at) "
        "VALUES (?, ?, ?)",
        (label, image_url, datetime.datetime.now(datetime.UTC).isoformat()),
    )
    log_admin_activity(
        "add_avatar",
        "avatar",
        None,
        f"Added profile picture: {label}",
    )
    db.commit()
    flash("Profile picture added.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/lulu", methods=["POST"])
@admin_required
def lulu():
    message, error = apply_lulu_command(request.form.get("prompt", ""))
    if error:
        return render_template(
            "admin.html",
            projects=get_db().execute("SELECT * FROM projects ORDER BY id DESC").fetchall(),
            users=get_db().execute("SELECT * FROM users ORDER BY last_login DESC").fetchall(),
            avatars=get_db().execute("SELECT * FROM avatar_options ORDER BY id DESC").fetchall(),
            activities=recent_admin_activity(),
            player_activities=recent_player_activity(),
            security_alerts=recent_security_alerts(),
            bans=get_db().execute(
                "SELECT b.*, u.name, u.username, u.email, u.is_admin FROM user_bans b "
                "JOIN users u ON u.id = b.user_id WHERE b.lifted_at IS NULL ORDER BY b.id DESC"
            ).fetchall(),
            q="",
            lulu_message=error,
        ), 400
    log_admin_activity("lulu_command", "settings", None, message or "")
    get_db().commit()
    return render_template(
        "admin.html",
        projects=get_db().execute("SELECT * FROM projects ORDER BY id DESC").fetchall(),
        users=get_db().execute("SELECT * FROM users ORDER BY last_login DESC").fetchall(),
        avatars=get_db().execute("SELECT * FROM avatar_options ORDER BY id DESC").fetchall(),
        activities=recent_admin_activity(),
        player_activities=recent_player_activity(),
        security_alerts=recent_security_alerts(),
        bans=get_db().execute(
            "SELECT b.*, u.name, u.username, u.email, u.is_admin FROM user_bans b "
            "JOIN users u ON u.id = b.user_id WHERE b.lifted_at IS NULL ORDER BY b.id DESC"
        ).fetchall(),
        q="",
        lulu_message=message,
    )


@app.route("/robots.txt")
def robots_txt():
    lines = [
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {request.url_root.rstrip('/')}/sitemap.xml",
    ]
    return "\n".join(lines), 200, {"Content-Type": "text/plain"}


@app.route("/sitemap.xml")
def sitemap():
    db = get_db()
    rows = db.execute("SELECT id FROM projects ORDER BY id DESC").fetchall()
    urls = [url_for("index", _external=True)]
    for row in rows:
        urls.append(url_for("view_project", pid=row["id"], _external=True))
    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        xml_lines.append("  <url>")
        xml_lines.append(f"    <loc>{u}</loc>")
        xml_lines.append("  </url>")
    xml_lines.append("</urlset>")
    return "\n".join(xml_lines), 200, {"Content-Type": "application/xml"}


if __name__ == "__main__":
    with app.app_context():
        init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
