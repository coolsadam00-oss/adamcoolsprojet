import io
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

import app as site


ADMIN_EMAIL = "coolsadam00@gmail.com"


class SiteAuthAdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        site.DB_PATH = os.path.join(self.tmp, "projects.db")
        site.PROJECTS_DIR = os.path.join(self.tmp, "projects")
        os.makedirs(site.PROJECTS_DIR, exist_ok=True)
        site.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with site.app.app_context():
            site.init_db()
        self.client = site.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def login(self, email=ADMIN_EMAIL, name="Admin User"):
        with site.app.app_context():
            user = site.upsert_user(email=email, name=name)
            user_id = user["id"]
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
        return user_id

    def make_zip(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as zf:
            zf.writestr("index.html", "<h1>Game</h1>")
        payload.seek(0)
        return payload

    def test_home_allows_guest_browsing(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Sign in", response.data)

    def test_guest_can_view_project(self):
        with site.app.app_context():
            db = site.get_db()
            cur = db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Guest Game", "", "", "1", "now"),
            )
            db.commit()
            project_id = cur.lastrowid
            folder = os.path.join(site.PROJECTS_DIR, str(project_id))
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as f:
                f.write("<h1>Guest Game</h1>")

        response = self.client.get(f"/project/{project_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Guest Game", response.data)

    def test_seed_admin_email_is_admin(self):
        with site.app.app_context():
            user = site.upsert_user(email=ADMIN_EMAIL, name="Adam")

        self.assertEqual(user["email"], ADMIN_EMAIL)
        self.assertEqual(user["is_admin"], 1)

    def test_upload_requires_admin(self):
        self.login("player@example.com", "Player")

        response = self.client.get("/upload")

        self.assertEqual(response.status_code, 403)

    def test_google_login_uses_https_callback_and_saves_next_url(self):
        site.app.config["PREFERRED_URL_SCHEME"] = "https"
        with mock.patch.dict(
            os.environ,
            {
                "GOOGLE_CLIENT_ID": "client-id",
                "GOOGLE_CLIENT_SECRET": "client-secret",
            },
        ):
            response = self.client.get("/auth/google?next=/admin")

        self.assertEqual(response.status_code, 302)
        self.assertIn("redirect_uri=https%3A%2F%2Flocalhost%2Fauth%2Fgoogle%2Fcallback", response.headers["Location"])
        with self.client.session_transaction() as session:
            self.assertEqual(session["oauth_next"], "/admin")

    def test_admin_can_upload_game_with_thumbnail(self):
        self.login()

        response = self.client.post(
            "/upload",
            data={
                "title": "Space Run",
                "description": "Fast arcade game",
                "tags": "arcade,space",
                "file": (self.make_zip(), "space.zip"),
                "thumbnail": (io.BytesIO(b"fake-png"), "thumb.png"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            row = site.get_db().execute("SELECT * FROM projects").fetchone()
        self.assertEqual(row["title"], "Space Run")
        self.assertTrue(row["thumbnail"].endswith("thumbnail.png"))

    def test_admin_can_delete_project(self):
        self.login()
        with site.app.app_context():
            db = site.get_db()
            cur = db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Old Game", "", "", "1", "now"),
            )
            db.commit()
            project_id = cur.lastrowid

        response = self.client.post(f"/admin/projects/{project_id}/delete")

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            row = site.get_db().execute(
                "SELECT * FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        self.assertIsNone(row)

    def test_admin_can_promote_user(self):
        self.login()
        with site.app.app_context():
            user = site.upsert_user("friend@example.com", "Friend")

        response = self.client.post(f"/admin/users/{user['id']}/make-admin")

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            row = site.get_db().execute(
                "SELECT is_admin FROM users WHERE id = ?",
                (user["id"],),
            ).fetchone()
        self.assertEqual(row["is_admin"], 1)

    def test_lulu_can_change_site_name_but_rejects_code_changes(self):
        self.login()

        rename_response = self.client.post(
            "/admin/lulu",
            data={"prompt": "change site name to Cool Games"},
        )
        code_response = self.client.post(
            "/admin/lulu",
            data={"prompt": "edit app.py and run server commands"},
        )

        self.assertEqual(rename_response.status_code, 200)
        self.assertIn(b"Cool Games", rename_response.data)
        self.assertEqual(code_response.status_code, 400)
        self.assertIn(b"not allowed", code_response.data)


if __name__ == "__main__":
    unittest.main()
