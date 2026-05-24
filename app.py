import os
import sqlite3
import zipfile
import datetime
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
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("RENDER_DATA_DIR", os.path.join(BASE_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "projects.db")
PROJECTS_DIR = os.path.join(DATA_DIR, "projects")
os.makedirs(PROJECTS_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = "dev-key-for-local"  # change in production
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB uploads
app.config["SITE_NAME"] = "adamcoolsprojet.com"


@app.context_processor
def inject_site_name():
    return {"site_name": app.config.get("SITE_NAME", "My Games")}


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
    db.commit()


@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, "db", None)
    if db is not None:
        db.close()


def safe_extract_zip(zipf: zipfile.ZipFile, target_dir: str):
    for name in zipf.namelist():
        if os.path.isabs(name) or ".." in name:
            raise Exception("Unsafe file path in zip")
    zipf.extractall(target_dir)


@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    db = get_db()
    if q:
        like = f"%{q}%"
        rows = db.execute(
            "SELECT * FROM projects WHERE title LIKE ? OR description LIKE ? OR tags LIKE ? ORDER BY id DESC",
            (like, like, like),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM projects ORDER BY id DESC").fetchall()
    projects = [dict(r) for r in rows]
    return render_template("index.html", projects=projects, q=q)


@app.route("/upload", methods=["GET", "POST"])
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
        created_at = datetime.datetime.utcnow().isoformat()
        cur = db.execute(
            "INSERT INTO projects (title, description, tags, folder, created_at) VALUES (?, ?, ?, ?, ?)",
            (title, description, tags, "", created_at),
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

        db.execute("UPDATE projects SET folder = ? WHERE id = ?", (str(pid), pid))
        db.commit()

        flash("Project uploaded successfully.")
        return redirect(url_for("index"))

    return render_template("upload.html")


@app.route("/project/<int:pid>")
def view_project(pid):
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    if not row:
        abort(404)
    project = dict(row)

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

    return render_template("view.html", project=project, index_file=found)


@app.route("/project_files/<int:pid>/<path:filename>")
def project_files(pid, filename):
    folder = os.path.join(PROJECTS_DIR, str(pid))
    full = os.path.join(folder, filename)
    if not os.path.commonpath([os.path.abspath(full), folder]) == os.path.abspath(folder):
        abort(403)
    if not os.path.exists(full):
        abort(404)
    return send_from_directory(folder, filename)


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
