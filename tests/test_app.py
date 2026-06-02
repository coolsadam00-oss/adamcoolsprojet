import io
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

import app as site


ADMIN_EMAIL = "coolsadam00@gmail.com"


class SiteStorageConfigTests(unittest.TestCase):
    def test_resolve_data_dir_uses_persistent_env_var(self):
        data_dir = site.resolve_data_dir(
            {"ADAM_DATA_DIR": "/srv/adam-data", "RENDER_DATA_DIR": "/var/data"},
            "/app",
        )

        self.assertEqual(data_dir, "/srv/adam-data")

    def test_resolve_data_dir_uses_render_disk_env_var(self):
        data_dir = site.resolve_data_dir({"RENDER_DATA_DIR": "/var/data"}, "/app")

        self.assertEqual(data_dir, "/var/data")

    def test_resolve_data_dir_defaults_to_project_data_folder(self):
        data_dir = site.resolve_data_dir({}, "/app")

        self.assertEqual(data_dir, os.path.join("/app", "data"))

    def test_render_blueprint_mounts_persistent_disk_for_app_data(self):
        with open("render.yaml", encoding="utf-8") as render_file:
            blueprint = render_file.read()

        self.assertIn("RENDER_DATA_DIR", blueprint)
        self.assertIn("    disk:", blueprint)
        self.assertIn("mountPath: /var/data", blueprint)


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

    def test_premade_admin_account_can_sign_in(self):
        response = self.client.post(
            "/login",
            data={"email": "ADMINADAM2155", "password": "Brosky2155"},
        )

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertIn("user_id", session)
        with site.app.app_context():
            user = site.get_db().execute(
                "SELECT * FROM users WHERE id = ?",
                (session["user_id"],),
            ).fetchone()
        self.assertEqual(user["email"], ADMIN_EMAIL)
        self.assertEqual(user["is_admin"], 1)
        self.assertEqual(user["email_verified"], 1)

    def test_login_page_accepts_admin_login_name(self):
        response = self.client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="email" type="text"', response.data)

    def test_premade_admin_login_repairs_existing_bad_account_state(self):
        with site.app.app_context():
            db = site.get_db()
            db.execute(
                "UPDATE users SET password_hash = ?, email_verified = 0, "
                "is_admin = 0 WHERE email = ?",
                ("broken-password", ADMIN_EMAIL),
            )
            db.commit()

        response = self.client.post(
            "/login",
            data={"email": ADMIN_EMAIL, "password": "Brosky2155"},
        )

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertIn("user_id", session)
        with site.app.app_context():
            user = site.get_db().execute(
                "SELECT * FROM users WHERE email = ?",
                (ADMIN_EMAIL,),
            ).fetchone()
        self.assertEqual(user["is_admin"], 1)
        self.assertEqual(user["email_verified"], 1)

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

    def test_signup_rejects_mismatched_passwords(self):
        response = self.client.post(
            "/signup",
            data={
                "email": "new@example.com",
                "password": "secret123",
                "confirm_password": "different",
            },
        )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            user = site.get_db().execute(
                "SELECT * FROM users WHERE email = ?",
                ("new@example.com",),
            ).fetchone()
        self.assertIsNone(user)

    def test_signup_requires_terms_agreement(self):
        response = self.client.post(
            "/signup",
            data={
                "email": "new@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
            },
        )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            user = site.get_db().execute(
                "SELECT * FROM users WHERE email = ?",
                ("new@example.com",),
            ).fetchone()
        self.assertIsNone(user)

    def test_signup_creates_unverified_user_and_sends_email(self):
        sent = []
        with mock.patch.object(site, "send_verification_email", side_effect=lambda email, token: sent.append((email, token))):
            response = self.client.post(
                "/signup",
                data={
                    "email": "new@example.com",
                    "password": "secret123",
                    "confirm_password": "secret123",
                    "agree_terms": "on",
                },
            )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            user = site.get_db().execute(
                "SELECT * FROM users WHERE email = ?",
                ("new@example.com",),
            ).fetchone()
        self.assertEqual(user["email_verified"], 0)
        self.assertTrue(user["password_hash"])
        self.assertEqual(sent[0][0], "new@example.com")
        self.assertEqual(sent[0][1], user["verification_token"])

    def test_verify_email_allows_password_login(self):
        with mock.patch.object(site, "send_verification_email"):
            self.client.post(
                "/signup",
                data={
                    "email": "new@example.com",
                    "password": "secret123",
                    "confirm_password": "secret123",
                    "agree_terms": "on",
                },
            )
        with site.app.app_context():
            token = site.get_db().execute(
                "SELECT verification_token FROM users WHERE email = ?",
                ("new@example.com",),
            ).fetchone()["verification_token"]

        verify_response = self.client.get(f"/verify-email/{token}")
        login_response = self.client.post(
            "/login",
            data={"email": "new@example.com", "password": "secret123"},
        )

        self.assertEqual(verify_response.status_code, 302)
        self.assertEqual(login_response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertIn("user_id", session)

    def test_remember_device_makes_login_session_permanent(self):
        response = self.client.post(
            "/login",
            data={
                "email": ADMIN_EMAIL,
                "password": "Brosky2155",
                "remember_device": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertTrue(session["_permanent"])

    def test_unverified_user_cannot_password_login(self):
        with mock.patch.object(site, "send_verification_email"):
            self.client.post(
                "/signup",
                data={
                    "email": "new@example.com",
                    "password": "secret123",
                    "confirm_password": "secret123",
                    "agree_terms": "on",
                },
            )

        response = self.client.post(
            "/login",
            data={"email": "new@example.com", "password": "secret123"},
        )

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)

    def test_unverified_user_can_request_another_verification_email(self):
        sent = []
        with mock.patch.object(site, "send_verification_email"):
            self.client.post(
                "/signup",
                data={
                    "email": "new@example.com",
                    "password": "secret123",
                    "confirm_password": "secret123",
                    "agree_terms": "on",
                },
            )

        with mock.patch.object(site, "send_verification_email", side_effect=lambda email, token: sent.append((email, token))):
            response = self.client.post(
                "/resend-verification",
                data={"email": "new@example.com"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(sent[0][0], "new@example.com")
        with site.app.app_context():
            user = site.get_db().execute(
                "SELECT verification_token FROM users WHERE email = ?",
                ("new@example.com",),
            ).fetchone()
        self.assertEqual(sent[0][1], user["verification_token"])

    def test_signed_in_user_can_delete_own_account(self):
        user_id = self.login("delete-me@example.com", "Delete Me")

        response = self.client.post("/account/delete")

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            user = site.get_db().execute(
                "SELECT * FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        self.assertIsNone(user)
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)

    def test_guest_cannot_delete_account(self):
        response = self.client.post("/account/delete")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_signed_in_user_can_update_public_username(self):
        user_id = self.login("player@example.com", "Player")

        response = self.client.post(
            "/account/profile",
            data={"username": "Cool Player"},
        )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            user = site.get_db().execute(
                "SELECT name FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        self.assertEqual(user["name"], "Cool Player")

    def test_signed_in_user_can_choose_offered_profile_picture(self):
        user_id = self.login("player@example.com", "Player")
        with site.app.app_context():
            db = site.get_db()
            cur = db.execute(
                "INSERT INTO avatar_options (label, image_url, created_at) "
                "VALUES (?, ?, ?)",
                ("Blue Bolt", "/static/test-avatar.png", "now"),
            )
            db.commit()
            avatar_id = cur.lastrowid

        response = self.client.post(
            "/account/profile",
            data={
                "username": "Cool Player",
                "avatar_id": str(avatar_id),
            },
        )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            user = site.get_db().execute(
                "SELECT picture FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        self.assertEqual(user["picture"], "/static/test-avatar.png")

    def test_user_cannot_choose_unlisted_profile_picture(self):
        user_id = self.login("player@example.com", "Player")

        response = self.client.post(
            "/account/profile",
            data={"username": "Cool Player", "avatar_id": "999"},
        )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            user = site.get_db().execute(
                "SELECT picture FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        self.assertEqual(user["picture"], "")

    def test_home_search_finds_public_usernames(self):
        with site.app.app_context():
            site.upsert_user("searchable@example.com", "Cool Player")

        response = self.client.get("/?q=Cool")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Players", response.data)
        self.assertIn(b"Cool Player", response.data)

    def test_home_search_hides_emails_from_non_admins(self):
        with site.app.app_context():
            site.upsert_user("searchable@example.com", "Cool Player")

        response = self.client.get("/?q=searchable@example.com")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Players", response.data)
        self.assertIn(b"Cool Player", response.data)
        self.assertIn(b"Regular player", response.data)
        self.assertNotIn(b"searchable@example.com", response.data)

    def test_home_search_shows_admin_role_to_non_admins(self):
        with site.app.app_context():
            site.upsert_user(ADMIN_EMAIL, "Adam")

        response = self.client.get("/?q=Adam")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Adam", response.data)
        self.assertIn(b"Admin", response.data)
        self.assertNotIn(ADMIN_EMAIL.encode(), response.data)

    def test_home_search_shows_emails_to_admins(self):
        self.login()
        with site.app.app_context():
            site.upsert_user("searchable@example.com", "Cool Player")

        response = self.client.get("/?q=Cool")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Cool Player", response.data)
        self.assertIn(b"searchable@example.com", response.data)

    def test_signed_in_user_can_rate_game(self):
        user_id = self.login("player@example.com", "Player")
        with site.app.app_context():
            db = site.get_db()
            cur = db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Rate Me", "", "", "1", "now"),
            )
            db.commit()
            project_id = cur.lastrowid

        response = self.client.post(
            f"/project/{project_id}/rate",
            data={"score": "5"},
        )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            rating = site.get_db().execute(
                "SELECT score FROM ratings WHERE project_id = ? AND user_id = ?",
                (project_id, user_id),
            ).fetchone()
        self.assertEqual(rating["score"], 5)

        page = self.client.get(f"/project/{project_id}")
        self.assertIn(b"5.0", page.data)
        self.assertIn(b"Your rating", page.data)

    def test_legal_pages_render(self):
        terms = self.client.get("/terms")
        privacy = self.client.get("/privacy")

        self.assertEqual(terms.status_code, 200)
        self.assertEqual(privacy.status_code, 200)
        self.assertIn(b"Website Rules", terms.data)
        self.assertIn(b"Privacy Policy", privacy.data)

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

    def test_admin_can_add_profile_picture_option(self):
        self.login()

        response = self.client.post(
            "/admin/avatars",
            data={
                "label": "Fire Mode",
                "image_url": "https://example.com/fire.png",
            },
        )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            avatar = site.get_db().execute(
                "SELECT * FROM avatar_options WHERE label = ?",
                ("Fire Mode",),
            ).fetchone()
        self.assertEqual(avatar["image_url"], "https://example.com/fire.png")

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
