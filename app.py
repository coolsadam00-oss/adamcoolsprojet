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
RENDER_DATA = os.environ.get("RENDER_DATA_DIR")
if RENDER_DATA:
    DATA_DIR = RENDER_DATA
else:
    import tempfile

    DATA_DIR = os.path.join(tempfile.gettempdir(), "adamcoolsprojet_data")

DB_PATH = os.path.join(DATA_DIR, "projects.db")
PROJECTS_DIR = os.path.join(DATA_DIR, "projects")
os.makedirs(PROJECTS_DIR, exist_ok=True)

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
    return db


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
            created_at TEXT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
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
    columns = {
        row["name"] for row in db.execute("PRAGMA table_info(projects)").fetchall()
    }
    if "thumbnail" not in columns:
        db.execute("ALTER TABLE projects ADD COLUMN thumbnail TEXT")
    seed_default_avatars(db)
    seed_premade_account(db)
    db.commit()


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
            "is_admin = 1, password_hash = ?, email_verified = 1, "
            "verification_token = NULL WHERE id = ?",
            (ADMIN_EMAIL.split("@")[0], password_hash, existing["id"]),
        )
        return
    db.execute(
        "INSERT INTO users (email, name, picture, is_admin, created_at, last_login, "
        "password_hash, email_verified, verification_token) "
        "VALUES (?, ?, ?, 1, ?, ?, ?, 1, NULL)",
        (
            ADMIN_EMAIL,
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
    now = datetime.datetime.now(datetime.UTC).isoformat()
    is_seed_admin = 1 if email == ADMIN_EMAIL else 0
    db = get_db()
    existing = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        db.execute(
            "UPDATE users SET name = ?, picture = ?, last_login = ?, "
            "email_verified = 1, "
            "is_admin = CASE WHEN email = ? THEN 1 ELSE is_admin END WHERE id = ?",
            (name or existing["name"], picture or existing["picture"], now, ADMIN_EMAIL, existing["id"]),
        )
    else:
        db.execute(
            "INSERT INTO users (email, name, picture, is_admin, created_at, last_login, email_verified) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (email, name, picture, is_seed_admin, now, now, 1),
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


def display_name(user):
    if not user:
        return ""
    return user["name"] or user["email"].split("@")[0]


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


def create_password_user(email, password):
    email = email.strip().lower()
    token = secrets.token_urlsafe(32)
    now = datetime.datetime.now(datetime.UTC).isoformat()
    db = get_db()
    existing = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    password_hash = generate_password_hash(password)
    is_seed_admin = 1 if email == ADMIN_EMAIL else 0
    if existing:
        db.execute(
            "UPDATE users SET password_hash = ?, verification_token = ?, "
            "email_verified = 0, is_admin = CASE WHEN email = ? THEN 1 ELSE is_admin END "
            "WHERE id = ?",
            (password_hash, token, ADMIN_EMAIL, existing["id"]),
        )
    else:
        db.execute(
            "INSERT INTO users (email, name, picture, is_admin, created_at, last_login, "
            "password_hash, email_verified, verification_token) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (email, email.split("@")[0], "", is_seed_admin, now, now, password_hash, token),
        )
    db.commit()
    return db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


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


def authenticate_password_user(email, password):
    db = get_db()
    email = email.strip().lower()
    if email == ADMIN_LOGIN.lower():
        email = ADMIN_EMAIL
    if email == ADMIN_EMAIL:
        seed_premade_account(db)
        db.commit()
    user = db.execute(
        "SELECT * FROM users WHERE email = ?",
        (email,),
    ).fetchone()
    if not user or not user["password_hash"]:
        return None, "Email or password is wrong."
    if not check_password_hash(user["password_hash"], password):
        return None, "Email or password is wrong."
    if not user["email_verified"]:
        return None, "Your account exists, but the email is not verified yet. Check your inbox or resend the link."
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
            "SELECT id, name, email, picture FROM users "
            "WHERE email_verified = 1 AND (name LIKE ? OR email LIKE ?) "
            "ORDER BY name LIMIT 12",
            (like, like),
        ).fetchall()
    else:
        users = []
    projects = [dict(r) for r in rows]
    return render_template("index.html", projects=projects, users=users, q=q)


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
        return redirect(safe_next_url(request.form.get("next")))

    return render_template(
        "login.html",
        google_ready=bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET")),
        next_url=safe_next_url(request.args.get("next")),
    )


@app.route("/signup", methods=["POST"])
def signup():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")
    agree_terms = request.form.get("agree_terms")
    if not email or "@" not in email:
        flash("Enter a valid email address.")
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

    user = create_password_user(email, password)
    send_verification_email(user["email"], user["verification_token"])
    flash("Account created. Check your inbox, then click the verification link.")
    return redirect(url_for("login"))


@app.route("/resend-verification", methods=["POST"])
def resend_verification():
    email = request.form.get("email", "").strip().lower()
    user = get_db().execute(
        "SELECT * FROM users WHERE email = ?",
        (email,),
    ).fetchone()
    if not user:
        flash("No account was found for that email.")
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
    avatars = get_db().execute(
        "SELECT * FROM avatar_options ORDER BY id",
    ).fetchall()
    return render_template("account.html", avatars=avatars)


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


@app.route("/upload", methods=["GET", "POST"])
@admin_required
def upload():
    if request.method == "POST":
        title = request.form.get("title", "Untitled").strip()
        description = request.form.get("description", "").strip()
        tags = request.form.get("tags", "").strip()
        file = request.files.get("file")
        if not file:
            flash("Please choose a zip file containing your project (HTML/CSS/JS).")
            return redirect(request.url)
        if not file.filename.lower().endswith(".zip"):
            flash("Only .zip uploads are accepted for now.")
            return redirect(request.url)

        db = get_db()
        created_at = datetime.datetime.now(datetime.UTC).isoformat()
        cur = db.execute(
            "INSERT INTO projects (title, description, tags, folder, created_at, thumbnail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (title, description, tags, "", created_at, ""),
        )
        db.commit()
        pid = cur.lastrowid
        folder = os.path.join(PROJECTS_DIR, str(pid))
        os.makedirs(folder, exist_ok=True)

        zip_path = os.path.join(folder, "upload.zip")
        file.save(zip_path)

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                safe_extract_zip(zf, folder)
            thumbnail = save_thumbnail(request.files.get("thumbnail"), folder)
        except Exception as e:
            db.execute("DELETE FROM projects WHERE id = ?", (pid,))
            db.commit()
            flash("Failed to extract zip: " + str(e))
            return redirect(request.url)

        # remove uploaded zip to save space
        try:
            os.remove(zip_path)
        except Exception:
            pass

        db.execute(
            "UPDATE projects SET folder = ?, thumbnail = ? WHERE id = ?",
            (str(pid), thumbnail, pid),
        )
        db.commit()

        flash("Project uploaded successfully.")
        return redirect(url_for("index"))

    return render_template("upload.html")


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
    if current_user():
        rating = db.execute(
            "SELECT score FROM ratings WHERE project_id = ? AND user_id = ?",
            (pid, current_user()["id"]),
        ).fetchone()
        if rating:
            user_rating = rating["score"]

    # find index.html inside folder
    folder = os.path.join(PROJECTS_DIR, str(pid))
    index_candidates = ["index.html", "index.htm", "game.html"]
    found = None
    for root, dirs, files in os.walk(folder):
        for cand in index_candidates:
            if cand in files:
                rel = os.path.relpath(os.path.join(root, cand), folder)
                found = rel.replace("\\", "/")
                break
        if found:
            break

    return render_template(
        "view.html",
        project=project,
        index_file=found,
        user_rating=user_rating,
    )


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
    db.commit()
    flash("Rating saved.")
    return redirect(url_for("view_project", pid=pid))


@app.route("/project_files/<int:pid>/<path:filename>")
def project_files(pid, filename):
    folder = os.path.join(PROJECTS_DIR, str(pid))
    full = os.path.join(folder, filename)
    if not os.path.commonpath([os.path.abspath(full), folder]) == os.path.abspath(folder):
        abort(403)
    if not os.path.exists(full):
        abort(404)
    return send_from_directory(folder, filename)


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
            "SELECT * FROM users WHERE email LIKE ? OR name LIKE ? ORDER BY last_login DESC",
            (like, like),
        ).fetchall()
    else:
        projects = db.execute("SELECT * FROM projects ORDER BY id DESC").fetchall()
        users = db.execute("SELECT * FROM users ORDER BY last_login DESC").fetchall()
    avatars = db.execute("SELECT * FROM avatar_options ORDER BY id DESC").fetchall()
    return render_template(
        "admin.html",
        projects=projects,
        users=users,
        avatars=avatars,
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
    db.commit()
    flash("Game removed.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/users/<int:user_id>/make-admin", methods=["POST"])
@admin_required
def make_admin(user_id):
    get_db().execute("UPDATE users SET is_admin = 1 WHERE id = ?", (user_id,))
    get_db().commit()
    flash("User is now an admin.")
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
    get_db().execute(
        "INSERT OR IGNORE INTO avatar_options (label, image_url, created_at) "
        "VALUES (?, ?, ?)",
        (label, image_url, datetime.datetime.now(datetime.UTC).isoformat()),
    )
    get_db().commit()
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
            q="",
            lulu_message=error,
        ), 400
    return render_template(
        "admin.html",
        projects=get_db().execute("SELECT * FROM projects ORDER BY id DESC").fetchall(),
        users=get_db().execute("SELECT * FROM users ORDER BY last_login DESC").fetchall(),
        avatars=get_db().execute("SELECT * FROM avatar_options ORDER BY id DESC").fetchall(),
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
