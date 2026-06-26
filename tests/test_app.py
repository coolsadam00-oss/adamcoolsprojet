import io
import os
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from unittest import mock

import app as site
from werkzeug.security import check_password_hash


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
        site.BACKUPS_DIR = os.path.join(self.tmp, "backups")
        os.makedirs(site.PROJECTS_DIR, exist_ok=True)
        os.makedirs(site.BACKUPS_DIR, exist_ok=True)
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

    def make_zip(self, filename="index.html", body="<h1>Game</h1>"):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as zf:
            zf.writestr(filename, body)
        payload.seek(0)
        return payload

    def test_home_allows_guest_browsing(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Sign in", response.data)

    def test_home_search_has_game_suggestions_ui(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="searchInput"', response.data)
        self.assertIn(b'id="searchSuggestions"', response.data)
        self.assertIn(b"search-suggestion-thumb", response.data)
        self.assertIn(b"item.image", response.data)
        self.assertIn(b'mouseenter"', response.data)
        self.assertIn(b'mouseleave"', response.data)
        self.assertNotIn(b"document.activeElement !== input", response.data)
        self.assertIn(b'closest("form")', response.data)
        self.assertNotIn(b'closest(".search")', response.data)

    def test_search_suggestions_returns_first_three_games_or_users(self):
        with site.app.app_context():
            db = site.get_db()
            for project_id, title, description in [
                (10, "Space Runner", "Fast space game"),
                (11, "Space Builder", "Build in space"),
                (12, "Space Adventure", "Explore space"),
                (13, "Space Extra", "Fourth result"),
            ]:
                db.execute(
                    "INSERT INTO projects (id, title, description, tags, folder, created_at, thumbnail) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (project_id, title, description, "space,arcade", str(project_id), "now", "thumb.png"),
                )
            site.upsert_user("spacefan@example.com", "Space Fan", "/static/avatar.png")
            db.commit()

        response = self.client.get("/search/suggestions?q=space")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["title"] for item in data["items"]], [
            "Space Runner",
            "Space Builder",
            "Space Adventure",
        ])
        self.assertIn("game", {item["type"] for item in data["items"]})
        self.assertLessEqual(len(data["items"]), 3)

    def test_search_suggestions_can_show_users_in_first_three_results(self):
        with site.app.app_context():
            db = site.get_db()
            site.upsert_user("alex@example.com", "Alex Player", "/static/avatar.png")
            db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Alex Game", "Game by Alex", "alex", "1", "now"),
            )
            db.commit()

        response = self.client.get("/search/suggestions?q=alex")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(data["items"]), 3)
        self.assertIn("user", {item["type"] for item in data["items"]})
        self.assertIn("Alex Player", [item["title"] for item in data["items"]])

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

    def test_project_view_has_fullscreen_button_for_game_frame(self):
        with site.app.app_context():
            db = site.get_db()
            cur = db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Fullscreen Game", "", "", "1", "now"),
            )
            db.commit()
            project_id = cur.lastrowid
            folder = os.path.join(site.PROJECTS_DIR, str(project_id))
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as f:
                f.write("<h1>Fullscreen Game</h1>")

        response = self.client.get(f"/project/{project_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="gameFrame"', response.data)
        self.assertIn(b'id="fullscreenButton"', response.data)
        self.assertIn(b"Fullscreen", response.data)
        self.assertIn(b"allowfullscreen", response.data)
        self.assertIn(b"requestFullscreen", response.data)

    def test_project_view_logs_player_monitoring_activity(self):
        self.login("player@example.com", "Player")
        with site.app.app_context():
            db = site.get_db()
            cur = db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Watched Game", "", "", "1", "now"),
            )
            db.commit()
            project_id = cur.lastrowid
            folder = os.path.join(site.PROJECTS_DIR, str(project_id))
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as f:
                f.write("<h1>Watched Game</h1>")

        response = self.client.get(f"/project/{project_id}")

        self.assertEqual(response.status_code, 200)
        with site.app.app_context():
            activity = site.get_db().execute(
                "SELECT * FROM player_activity WHERE project_id = ? ORDER BY id DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        self.assertEqual(activity["action"], "open_game_page")
        self.assertIn("Watched Game", activity["details"])

    def test_play_heartbeat_records_active_play_without_database_backup(self):
        self.login("player@example.com", "Player")
        with site.app.app_context():
            db = site.get_db()
            cur = db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Heartbeat Game", "", "", "1", "now"),
            )
            db.commit()
            project_id = cur.lastrowid

        response = self.client.post(f"/project/{project_id}/heartbeat")

        self.assertEqual(response.status_code, 204)
        with site.app.app_context():
            activity = site.get_db().execute(
                "SELECT * FROM player_activity WHERE project_id = ? ORDER BY id DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        self.assertEqual(activity["action"], "active_play")
        self.assertFalse(os.listdir(site.BACKUPS_DIR))

    def test_admin_panel_shows_player_monitoring_only_to_admins(self):
        player_id = self.login("player@example.com", "Player")
        with site.app.app_context():
            db = site.get_db()
            cur = db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Monitor Game", "", "", "1", "now"),
            )
            project_id = cur.lastrowid
            db.execute(
                "INSERT INTO player_activity "
                "(user_id, visitor_id, project_id, action, details, ip_address, user_agent, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (player_id, "visitor-1", project_id, "active_play", "Playing Monitor Game", "127.0.0.1", "Test Browser", "now"),
            )
            db.commit()

        regular_page = self.client.get("/admin")
        self.login()
        admin_page = self.client.get("/admin")

        self.assertEqual(regular_page.status_code, 403)
        self.assertEqual(admin_page.status_code, 200)
        self.assertIn(b"Player monitoring", admin_page.data)
        self.assertIn(b"Player", admin_page.data)
        self.assertIn(b"Monitor Game", admin_page.data)
        self.assertIn(b"active play", admin_page.data)

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

    def test_startup_keeps_existing_owner_password_hash(self):
        with site.app.app_context():
            db = site.get_db()
            custom_hash = site.generate_password_hash("imported-owner-pass")
            db.execute(
                "UPDATE users SET password_hash = ?, email_verified = 1 WHERE email = ?",
                (custom_hash, ADMIN_EMAIL),
            )
            db.commit()

            site.seed_premade_account(db)
            db.commit()
            user = db.execute(
                "SELECT * FROM users WHERE email = ?",
                (ADMIN_EMAIL,),
            ).fetchone()

        self.assertTrue(check_password_hash(user["password_hash"], "imported-owner-pass"))

    def test_upload_requires_admin(self):
        self.login("player@example.com", "Player")

        response = self.client.get("/upload")

        self.assertEqual(response.status_code, 403)

    def test_upload_page_mentions_3d_game_uploads(self):
        self.login()

        response = self.client.get("/upload")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Upload a 2D or 3D game", response.data)
        self.assertIn(b"2D or 3D HTML5 game ZIP", response.data)
        self.assertIn(b"Upload 2D / 3D Game", response.data)
        self.assertNotIn(b"Language", response.data)
        self.assertNotIn(b"Upload type", response.data)
        self.assertNotIn(b"Write code", response.data)
        self.assertNotIn(b"Entry file", response.data)

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
                "username": "newplayer",
                "password": "secret123",
                "confirm_password": "different",
            },
        )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            user = site.get_db().execute(
                "SELECT * FROM users WHERE username = ?",
                ("newplayer",),
            ).fetchone()
        self.assertIsNone(user)

    def test_signup_requires_terms_agreement(self):
        response = self.client.post(
            "/signup",
            data={
                "username": "newplayer",
                "password": "secret123",
                "confirm_password": "secret123",
            },
        )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            user = site.get_db().execute(
                "SELECT * FROM users WHERE username = ?",
                ("newplayer",),
            ).fetchone()
        self.assertIsNone(user)

    def test_signup_creates_username_account_without_email(self):
        response = self.client.post(
            "/signup",
            data={
                "username": "newplayer",
                "password": "secret123",
                "confirm_password": "secret123",
                "agree_terms": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            user = site.get_db().execute(
                "SELECT * FROM users WHERE username = ?",
                ("newplayer",),
            ).fetchone()
        self.assertEqual(user["name"], "newplayer")
        self.assertIsNone(user["email"])
        self.assertEqual(user["email_verified"], 0)
        self.assertTrue(user["password_hash"])

        login_response = self.client.post(
            "/login",
            data={"email": "newplayer", "password": "secret123"},
        )
        self.assertEqual(login_response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertIn("user_id", session)

    def test_signup_accepts_optional_safety_email_without_blocking_login(self):
        sent = []
        with mock.patch.object(site, "send_verification_email", side_effect=lambda email, token: sent.append((email, token))):
            response = self.client.post(
                "/signup",
                data={
                    "username": "newplayer",
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
        self.assertEqual(user["username"], "newplayer")
        self.assertTrue(user["password_hash"])
        self.assertEqual(sent[0][0], "new@example.com")
        self.assertEqual(sent[0][1], user["verification_token"])

        login_response = self.client.post(
            "/login",
            data={"email": "newplayer", "password": "secret123"},
        )
        self.assertEqual(login_response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertIn("user_id", session)

    def test_verify_email_marks_safety_email_verified(self):
        with mock.patch.object(site, "send_verification_email"):
            self.client.post(
                "/signup",
                data={
                    "username": "newplayer",
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

    def test_unverified_user_can_request_another_verification_email(self):
        sent = []
        with mock.patch.object(site, "send_verification_email"):
            self.client.post(
                "/signup",
                data={
                    "username": "newplayer",
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

    def test_user_can_send_and_accept_friend_request(self):
        player_id = self.login("player@example.com", "Player")
        with site.app.app_context():
            friend = site.upsert_user("friend@example.com", "Friend")

        send_response = self.client.post(f"/friends/{friend['id']}/request")
        with self.client.session_transaction() as session:
            session["user_id"] = friend["id"]
        accept_response = self.client.post(f"/friends/{player_id}/accept")

        self.assertEqual(send_response.status_code, 302)
        self.assertEqual(accept_response.status_code, 302)
        with site.app.app_context():
            friendship = site.get_db().execute(
                "SELECT status FROM friendships WHERE user_id = ? AND friend_id = ?",
                (min(player_id, friend["id"]), max(player_id, friend["id"])),
            ).fetchone()
        self.assertEqual(friendship["status"], "accepted")

    def test_private_message_requires_friendship(self):
        self.login("player@example.com", "Player")
        with site.app.app_context():
            friend = site.upsert_user("friend@example.com", "Friend")

        response = self.client.post(
            f"/messages/{friend['id']}",
            data={"body": "hello friend"},
        )

        self.assertEqual(response.status_code, 403)
        with site.app.app_context():
            count = site.get_db().execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        self.assertEqual(count, 0)

    def test_friends_can_send_safe_private_messages(self):
        player_id = self.login("player@example.com", "Player")
        with site.app.app_context():
            friend = site.upsert_user("friend@example.com", "Friend")
            site.create_friend_request(player_id, friend["id"])
            site.accept_friend_request(friend["id"], player_id)

        response = self.client.post(
            f"/messages/{friend['id']}",
            data={"body": "Want to play this game?"},
        )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            message = site.get_db().execute(
                "SELECT body FROM messages WHERE sender_id = ? AND recipient_id = ?",
                (player_id, friend["id"]),
            ).fetchone()
        self.assertEqual(message["body"], "Want to play this game?")

    def test_private_message_blocks_unsafe_text(self):
        player_id = self.login("player@example.com", "Player")
        with site.app.app_context():
            friend = site.upsert_user("friend@example.com", "Friend")
            site.create_friend_request(player_id, friend["id"])
            site.accept_friend_request(friend["id"], player_id)

        response = self.client.post(
            f"/messages/{friend['id']}",
            data={"body": "where do you live show your pics"},
        )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            count = site.get_db().execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        self.assertEqual(count, 0)

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

    def test_signed_in_user_can_comment_on_game(self):
        user_id = self.login("player@example.com", "Player")
        with site.app.app_context():
            db = site.get_db()
            cur = db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Comment Game", "", "", "1", "now"),
            )
            db.commit()
            project_id = cur.lastrowid

        response = self.client.post(
            f"/project/{project_id}/comments",
            data={"body": "This game is fun"},
        )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            comment = site.get_db().execute(
                "SELECT body, user_id FROM comments WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        self.assertEqual(comment["body"], "This game is fun")
        self.assertEqual(comment["user_id"], user_id)

        page = self.client.get(f"/project/{project_id}")
        self.assertIn(b"Comments", page.data)
        self.assertIn(b"This game is fun", page.data)
        self.assertIn(b"Player", page.data)

    def test_guest_cannot_comment_on_game(self):
        with site.app.app_context():
            db = site.get_db()
            cur = db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Comment Game", "", "", "1", "now"),
            )
            db.commit()
            project_id = cur.lastrowid

        response = self.client.post(
            f"/project/{project_id}/comments",
            data={"body": "Guest note"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])
        with site.app.app_context():
            count = site.get_db().execute(
                "SELECT COUNT(*) FROM comments WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_admin_can_delete_game_comment(self):
        self.login()
        with site.app.app_context():
            user = site.upsert_user("player@example.com", "Player")
            db = site.get_db()
            cur = db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Comment Game", "", "", "1", "now"),
            )
            project_id = cur.lastrowid
            comment = db.execute(
                "INSERT INTO comments (project_id, user_id, body, created_at) "
                "VALUES (?, ?, ?, ?)",
                (project_id, user["id"], "Please remove", "now"),
            )
            db.commit()
            comment_id = comment.lastrowid

        response = self.client.post(f"/comments/{comment_id}/delete")

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            comment = site.get_db().execute(
                "SELECT * FROM comments WHERE id = ?",
                (comment_id,),
            ).fetchone()
        self.assertIsNone(comment)

    def test_admin_comment_deletion_is_saved_in_activity_log(self):
        self.login()
        with site.app.app_context():
            user = site.upsert_user("player@example.com", "Player")
            db = site.get_db()
            cur = db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Comment Game", "", "", "1", "now"),
            )
            project_id = cur.lastrowid
            comment = db.execute(
                "INSERT INTO comments (project_id, user_id, body, created_at) "
                "VALUES (?, ?, ?, ?)",
                (project_id, user["id"], "Please remove", "now"),
            )
            db.commit()
            comment_id = comment.lastrowid

        response = self.client.post(f"/comments/{comment_id}/delete")

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            activity = site.get_db().execute(
                "SELECT action, target_type, target_id, details FROM admin_activity "
                "ORDER BY id DESC LIMIT 1",
            ).fetchone()
        self.assertEqual(activity["action"], "delete_comment")
        self.assertEqual(activity["target_type"], "comment")
        self.assertEqual(activity["target_id"], comment_id)
        self.assertIn("Comment Game", activity["details"])

    def test_regular_user_cannot_delete_game_comment(self):
        self.login("player@example.com", "Player")
        with site.app.app_context():
            admin = site.upsert_user(ADMIN_EMAIL, "Adam")
            db = site.get_db()
            cur = db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Comment Game", "", "", "1", "now"),
            )
            project_id = cur.lastrowid
            comment = db.execute(
                "INSERT INTO comments (project_id, user_id, body, created_at) "
                "VALUES (?, ?, ?, ?)",
                (project_id, admin["id"], "Keep this", "now"),
            )
            db.commit()
            comment_id = comment.lastrowid

        response = self.client.post(f"/comments/{comment_id}/delete")

        self.assertEqual(response.status_code, 403)
        with site.app.app_context():
            comment = site.get_db().execute(
                "SELECT * FROM comments WHERE id = ?",
                (comment_id,),
            ).fetchone()
        self.assertIsNotNone(comment)

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
                "platform_support": "mobile_pc",
                "confirm_upload": "on",
                "file": (self.make_zip(), "space.zip"),
                "thumbnail": (io.BytesIO(b"fake-png"), "thumb.png"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            row = site.get_db().execute("SELECT * FROM projects").fetchone()
        self.assertEqual(row["title"], "Space Run")
        self.assertEqual(row["platform_support"], "mobile_pc")
        self.assertEqual(row["source_zip"], "source.zip")
        self.assertTrue(row["thumbnail"].endswith("thumbnail.png"))

    def test_upload_requires_platform_support_choice_and_confirmation(self):
        self.login()

        missing_platform = self.client.post(
            "/upload",
            data={
                "title": "No Platform",
                "file": (self.make_zip(), "game.zip"),
                "confirm_upload": "on",
            },
            content_type="multipart/form-data",
        )
        missing_confirm = self.client.post(
            "/upload",
            data={
                "title": "No Confirm",
                "platform_support": "pc",
                "file": (self.make_zip(), "game.zip"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(missing_platform.status_code, 302)
        self.assertEqual(missing_confirm.status_code, 302)
        with site.app.app_context():
            count = site.get_db().execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        self.assertEqual(count, 0)

    def test_admin_upload_is_saved_in_activity_log(self):
        self.login()

        response = self.client.post(
            "/upload",
            data={
                "title": "Activity Space",
                "description": "Log this upload",
                "tags": "activity",
                "platform_support": "pc",
                "confirm_upload": "on",
                "file": (self.make_zip(), "activity.zip"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            activity = site.get_db().execute(
                "SELECT action, target_type, details FROM admin_activity "
                "ORDER BY id DESC LIMIT 1",
            ).fetchone()
        self.assertEqual(activity["action"], "upload_project")
        self.assertEqual(activity["target_type"], "project")
        self.assertIn("Activity Space", activity["details"])

    def test_saved_data_survives_app_setup_against_same_store(self):
        with site.app.app_context():
            user = site.upsert_user("persist@example.com", "Persist Player")
            db = site.get_db()
            cur = db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Persistent Game", "", "", "1", "now"),
            )
            project_id = cur.lastrowid
            folder = os.path.join(site.PROJECTS_DIR, str(project_id))
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as game_file:
                game_file.write("<h1>Persistent Game</h1>")
            db.execute(
                "INSERT INTO comments (project_id, user_id, body, created_at) "
                "VALUES (?, ?, ?, ?)",
                (project_id, user["id"], "Still here", "now"),
            )
            db.execute(
                "INSERT INTO admin_activity "
                "(user_id, action, target_type, target_id, details, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user["id"], "manual_check", "project", project_id, "Saved check", "now"),
            )
            db.commit()

        with site.app.app_context():
            site.init_db()
            db = site.get_db()
            project = db.execute(
                "SELECT * FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            account = db.execute(
                "SELECT * FROM users WHERE email = ?",
                ("persist@example.com",),
            ).fetchone()
            comment = db.execute(
                "SELECT * FROM comments WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            activity = db.execute(
                "SELECT * FROM admin_activity WHERE target_id = ?",
                (project_id,),
            ).fetchone()

        self.assertEqual(project["title"], "Persistent Game")
        self.assertEqual(account["name"], "Persist Player")
        self.assertEqual(comment["body"], "Still here")
        self.assertEqual(activity["action"], "manual_check")
        self.assertTrue(os.path.exists(os.path.join(folder, "index.html")))

    def test_successful_changes_create_database_backup_with_saved_data(self):
        self.login()

        upload_response = self.client.post(
            "/upload",
            data={
                "title": "Backup Game",
                "description": "Backup this",
                "tags": "backup",
                "platform_support": "mobile",
                "confirm_upload": "on",
                "file": (self.make_zip(), "backup.zip"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(upload_response.status_code, 302)
        with site.app.app_context():
            project = site.get_db().execute(
                "SELECT id FROM projects WHERE title = ?",
                ("Backup Game",),
            ).fetchone()
        self.login("commenter@example.com", "Commenter")
        comment_response = self.client.post(
            f"/project/{project['id']}/comments",
            data={"body": "Back this up too"},
        )

        self.assertEqual(comment_response.status_code, 302)
        backups = sorted(
            name for name in os.listdir(site.BACKUPS_DIR) if name.endswith(".db")
        )
        self.assertTrue(backups)
        backup_path = os.path.join(site.BACKUPS_DIR, backups[-1])
        backup = sqlite3.connect(backup_path)
        backup.row_factory = sqlite3.Row
        try:
            saved_project = backup.execute(
                "SELECT * FROM projects WHERE title = ?",
                ("Backup Game",),
            ).fetchone()
            saved_account = backup.execute(
                "SELECT * FROM users WHERE email = ?",
                ("commenter@example.com",),
            ).fetchone()
            saved_comment = backup.execute(
                "SELECT * FROM comments WHERE body = ?",
                ("Back this up too",),
            ).fetchone()
            saved_activity = backup.execute(
                "SELECT * FROM admin_activity WHERE action = ?",
                ("upload_project",),
            ).fetchone()
        finally:
            backup.close()
        self.assertEqual(saved_project["title"], "Backup Game")
        self.assertEqual(saved_project["platform_support"], "mobile")
        self.assertEqual(saved_account["name"], "Commenter")
        self.assertEqual(saved_comment["body"], "Back this up too")
        self.assertIn("Backup Game", saved_activity["details"])

    def test_admin_can_see_and_download_uploaded_source_zip(self):
        self.login()
        upload_response = self.client.post(
            "/upload",
            data={
                "title": "Downloadable",
                "platform_support": "pc",
                "confirm_upload": "on",
                "file": (self.make_zip(), "downloadable.zip"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(upload_response.status_code, 302)
        with site.app.app_context():
            project = site.get_db().execute(
                "SELECT * FROM projects WHERE title = ?",
                ("Downloadable",),
            ).fetchone()

        admin_page = self.client.get("/admin")
        source_link = f"/admin/projects/{project['id']}/source/{project['source_token']}.zip"
        old_download_response = self.client.get(f"/admin/projects/{project['id']}/source.zip")
        download_response = self.client.get(source_link)

        self.assertEqual(admin_page.status_code, 200)
        self.assertIn(b"Download source ZIP", admin_page.data)
        self.assertIn(source_link.encode(), admin_page.data)
        self.assertEqual(old_download_response.status_code, 404)
        self.assertEqual(download_response.status_code, 200)
        self.assertIn(b"Game", download_response.data)
        download_response.close()

    def test_public_source_zip_request_is_blocked_and_warns_admin(self):
        self.login()
        self.client.post(
            "/upload",
            data={
                "title": "Private Source",
                "platform_support": "pc",
                "confirm_upload": "on",
                "file": (self.make_zip(), "private.zip"),
            },
            content_type="multipart/form-data",
        )
        with site.app.app_context():
            project = site.get_db().execute(
                "SELECT * FROM projects WHERE title = ?",
                ("Private Source",),
            ).fetchone()

        with self.client.session_transaction() as session:
            session.clear()
        blocked_response = self.client.get(f"/project_files/{project['id']}/source.zip")
        self.login()
        admin_page = self.client.get("/admin")

        self.assertEqual(blocked_response.status_code, 404)
        self.assertIn(b"Security warnings", admin_page.data)
        self.assertIn(b"Blocked public source ZIP request", admin_page.data)

    def test_signed_in_user_can_favorite_game_and_see_it_on_account(self):
        user_id = self.login("player@example.com", "Player")
        with site.app.app_context():
            db = site.get_db()
            cur = db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Favorite Me", "", "", "1", "now"),
            )
            project_id = cur.lastrowid
            db.commit()

        favorite_response = self.client.post(f"/project/{project_id}/favorite")
        account_response = self.client.get("/account")

        self.assertEqual(favorite_response.status_code, 302)
        with site.app.app_context():
            favorite = site.get_db().execute(
                "SELECT * FROM favorites WHERE user_id = ? AND project_id = ?",
                (user_id, project_id),
            ).fetchone()
        self.assertIsNotNone(favorite)
        self.assertIn(b"Favorite Me", account_response.data)

    def test_account_shows_friend_points(self):
        player_id = self.login("player@example.com", "Player")
        with site.app.app_context():
            friend = site.upsert_user("friend@example.com", "Friend")
            site.create_friend_request(friend["id"], player_id)

        self.client.post(f"/friends/{friend['id']}/accept")
        response = self.client.get("/account")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Friend points", response.data)
        self.assertIn(b"10", response.data)

    def test_admin_can_replace_uploaded_game_zip(self):
        self.login()
        self.client.post(
            "/upload",
            data={
                "title": "Replace Me",
                "platform_support": "pc",
                "confirm_upload": "on",
                "file": (self.make_zip(), "old.zip"),
            },
            content_type="multipart/form-data",
        )
        replacement = io.BytesIO()
        with zipfile.ZipFile(replacement, "w") as zf:
            zf.writestr("index.html", "<h1>Updated Game</h1>")
        replacement.seek(0)
        with site.app.app_context():
            project = site.get_db().execute(
                "SELECT * FROM projects WHERE title = ?",
                ("Replace Me",),
            ).fetchone()

        response = self.client.post(
            f"/admin/projects/{project['id']}/replace",
            data={
                "platform_support": "mobile_pc",
                "confirm_upload": "on",
                "file": (replacement, "new.zip"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            updated = site.get_db().execute(
                "SELECT * FROM projects WHERE id = ?",
                (project["id"],),
            ).fetchone()
        self.assertEqual(updated["platform_support"], "mobile_pc")
        with open(os.path.join(site.PROJECTS_DIR, str(project["id"]), "index.html"), encoding="utf-8") as game_file:
            self.assertIn("Updated Game", game_file.read())

    def test_admin_can_update_project_name_tags_and_thumbnail(self):
        self.login()
        with site.app.app_context():
            db = site.get_db()
            cur = db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at, thumbnail) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("Old Title", "Old description", "old", "1", "now", "old-thumb.png"),
            )
            project_id = cur.lastrowid
            db.commit()
            folder = os.path.join(site.PROJECTS_DIR, str(project_id))
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "old-thumb.png"), "wb") as old_thumb:
                old_thumb.write(b"old")

        response = self.client.post(
            f"/admin/projects/{project_id}/metadata",
            data={
                "title": "New Title",
                "description": "New description",
                "tags": "#stealth #arcade, fun",
                "thumbnail": (io.BytesIO(b"new-thumb"), "cover.webp"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        with site.app.app_context():
            project = site.get_db().execute(
                "SELECT * FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        self.assertEqual(project["title"], "New Title")
        self.assertEqual(project["description"], "New description")
        self.assertEqual(project["tags"], "stealth,arcade,fun")
        self.assertEqual(project["thumbnail"], "thumbnail.webp")
        self.assertTrue(os.path.exists(os.path.join(site.PROJECTS_DIR, str(project_id), "thumbnail.webp")))

    def test_admin_project_editor_is_visible(self):
        self.login()
        with site.app.app_context():
            db = site.get_db()
            cur = db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Editable Game", "", "old", "1", "now"),
            )
            db.commit()
            project_id = cur.lastrowid

        response = self.client.get("/admin")

        self.assertEqual(response.status_code, 200)
        self.assertIn(f"/admin/projects/{project_id}/metadata".encode(), response.data)
        self.assertIn(b'name="title"', response.data)
        self.assertIn(b'name="tags"', response.data)
        self.assertIn(b'name="thumbnail"', response.data)

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

    def test_admin_can_download_website_data_zip(self):
        self.login()
        with site.app.app_context():
            db = site.get_db()
            user = site.create_password_user("exporttest", "secret123", "exporttest@example.com")
            db.execute(
                "INSERT INTO avatar_options (label, image_url, created_at) "
                "VALUES (?, ?, ?)",
                ("Saved Pic", "/project_files/1/thumb.png", "now"),
            )
            cur = db.execute(
                "INSERT INTO projects "
                "(title, description, tags, folder, created_at, thumbnail, source_zip) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("Data Game", "Backup item", "backup", "1", "now", "thumb.png", "source.zip"),
            )
            db.commit()
            project_id = cur.lastrowid
            db.execute(
                "UPDATE projects SET folder = ? WHERE id = ?",
                (str(project_id), project_id),
            )
            db.execute(
                "INSERT INTO favorites (user_id, project_id, created_at) VALUES (?, ?, ?)",
                (user["id"], project_id, "now"),
            )
            db.commit()
            folder = os.path.join(site.PROJECTS_DIR, str(project_id))
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as f:
                f.write("<h1>Data Game</h1>")
            with open(os.path.join(folder, "thumb.png"), "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n")
            with open(os.path.join(folder, "source.zip"), "wb") as f:
                f.write(b"source")

        response = self.client.get("/admin/export-website-data")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "application/zip")
        archive = zipfile.ZipFile(io.BytesIO(response.data))
        self.assertIn("projects.db", archive.namelist())
        self.assertIn("users.json", archive.namelist())
        self.assertIn(f"{project_id}/index.html", archive.namelist())
        self.assertIn(f"{project_id}/thumb.png", archive.namelist())
        self.assertIn(f"{project_id}/source.zip", archive.namelist())
        exported_db_path = os.path.join(self.tmp, "exported.db")
        with open(exported_db_path, "wb") as db_file:
            db_file.write(archive.read("projects.db"))
        exported = sqlite3.connect(exported_db_path)
        exported.row_factory = sqlite3.Row
        try:
            exported_user = exported.execute(
                "SELECT * FROM users WHERE username = ?",
                ("exporttest",),
            ).fetchone()
            exported_project = exported.execute(
                "SELECT * FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            exported_favorite = exported.execute(
                "SELECT * FROM favorites WHERE user_id = ? AND project_id = ?",
                (user["id"], project_id),
            ).fetchone()
            exported_avatar = exported.execute(
                "SELECT * FROM avatar_options WHERE label = ?",
                ("Saved Pic",),
            ).fetchone()
        finally:
            exported.close()
        self.assertEqual(exported_project["thumbnail"], "thumb.png")
        self.assertTrue(check_password_hash(exported_user["password_hash"], "secret123"))
        self.assertIsNotNone(exported_favorite)
        self.assertEqual(exported_avatar["image_url"], "/project_files/1/thumb.png")

    def test_admin_can_import_website_data_zip(self):
        self.login()
        with site.app.app_context():
            db = site.get_db()
            db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Original Game", "Original", "backup", "1", "now"),
            )
            db.commit()
            original_count = db.execute("SELECT COUNT(*) AS count FROM projects").fetchone()["count"]

        temp_db_fd, temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(temp_db_fd)
        try:
            conn = sqlite3.connect(temp_db_path)
            conn.execute(
                "CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT)"
            )
            conn.commit()
            conn.close()

            export_zip = io.BytesIO()
            with zipfile.ZipFile(export_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(temp_db_path, "projects.db")
            export_zip.seek(0)

            response = self.client.post(
                "/admin/import-website-data",
                data={"backup": (export_zip, "backup.zip")},
                content_type="multipart/form-data",
            )
        finally:
            os.unlink(temp_db_path)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin", response.headers["Location"])

    def test_admin_import_restores_full_exported_site_data(self):
        self.login()
        with site.app.app_context():
            db = site.get_db()
            user = site.create_password_user("savedplayer", "savedpass123", "saved@example.com")
            db.execute(
                "INSERT INTO avatar_options (label, image_url, created_at) VALUES (?, ?, ?)",
                ("Saved Avatar", "/static/saved-avatar.svg", "now"),
            )
            cur = db.execute(
                "INSERT INTO projects "
                "(title, description, tags, folder, created_at, thumbnail, source_zip, "
                "source_token, runtime_language, entry_file) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "Saved Game",
                    "Everything should come back",
                    "save",
                    "1",
                    "now",
                    "thumb.png",
                    "source.zip",
                    "tok",
                    "html",
                    "index.html",
                ),
            )
            project_id = cur.lastrowid
            db.execute("UPDATE projects SET folder = ? WHERE id = ?", (str(project_id), project_id))
            db.execute(
                "INSERT INTO comments (project_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
                (project_id, user["id"], "Saved comment", "now"),
            )
            db.commit()
            folder = os.path.join(site.PROJECTS_DIR, str(project_id))
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as f:
                f.write("<h1>Saved Game</h1>")
            with open(os.path.join(folder, "thumb.png"), "wb") as f:
                f.write(b"thumb")
            with open(os.path.join(folder, "source.zip"), "wb") as f:
                f.write(b"source")

        export_response = self.client.get("/admin/export-website-data")
        backup_zip = io.BytesIO(export_response.data)
        with site.app.app_context():
            db = site.get_db()
            db.execute("DELETE FROM comments")
            db.execute("DELETE FROM projects")
            db.execute("DELETE FROM avatar_options WHERE label = ?", ("Saved Avatar",))
            db.execute("DELETE FROM users WHERE username = ?", ("savedplayer",))
            db.commit()
        shutil.rmtree(site.PROJECTS_DIR)
        os.makedirs(site.PROJECTS_DIR, exist_ok=True)
        os.makedirs(os.path.join(site.PROJECTS_DIR, "stale"), exist_ok=True)
        backup_zip.seek(0)

        import_response = self.client.post(
            "/admin/import-website-data",
            data={"backup": (backup_zip, "website-data.zip")},
            content_type="multipart/form-data",
        )

        self.assertEqual(import_response.status_code, 302)
        with site.app.app_context():
            restored_user = site.get_db().execute(
                "SELECT * FROM users WHERE username = ?",
                ("savedplayer",),
            ).fetchone()
            restored_project = site.get_db().execute(
                "SELECT * FROM projects WHERE title = ?",
                ("Saved Game",),
            ).fetchone()
            restored_comment = site.get_db().execute(
                "SELECT * FROM comments WHERE body = ?",
                ("Saved comment",),
            ).fetchone()
            restored_avatar = site.get_db().execute(
                "SELECT * FROM avatar_options WHERE label = ?",
                ("Saved Avatar",),
            ).fetchone()
        self.assertTrue(check_password_hash(restored_user["password_hash"], "savedpass123"))
        self.assertEqual(restored_project["thumbnail"], "thumb.png")
        self.assertIsNotNone(restored_comment)
        self.assertEqual(restored_avatar["image_url"], "/static/saved-avatar.svg")
        self.assertTrue(os.path.exists(os.path.join(site.PROJECTS_DIR, str(restored_project["id"]), "index.html")))
        self.assertTrue(os.path.exists(os.path.join(site.PROJECTS_DIR, str(restored_project["id"]), "thumb.png")))
        self.assertTrue(os.path.exists(os.path.join(site.PROJECTS_DIR, str(restored_project["id"]), "source.zip")))
        self.assertFalse(os.path.exists(os.path.join(site.PROJECTS_DIR, "stale")))

        self.client.post("/logout")
        login_response = self.client.post(
            "/login",
            data={"email": "savedplayer", "password": "savedpass123"},
        )
        game_response = self.client.get(f"/project/{restored_project['id']}")

        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(game_response.status_code, 200)

    def test_banned_status_endpoint_reports_active_ban(self):
        self.login("badplayer@example.com", "Bad Player")
        with site.app.app_context():
            user = site.lookup_user_by_identifier("badplayer@example.com")
            site.get_db().execute(
                "INSERT INTO user_bans (user_id, reason, created_at, expires_at) "
                "VALUES (?, ?, ?, NULL)",
                (user["id"], "Breaking the rules", "now"),
            )
            site.get_db().commit()

        response = self.client.get("/banned-status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"banned": True})

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

    def test_admin_can_ban_regular_user_for_duration_and_unban(self):
        self.login()
        with site.app.app_context():
            user = site.upsert_user("badplayer@example.com", "Bad Player")

        ban_response = self.client.post(
            f"/admin/users/{user['id']}/ban",
            data={"duration": "1d", "reason": "Breaking rules"},
        )
        with site.app.app_context():
            active_ban = site.active_ban_for_user(user["id"])
        unban_response = self.client.post(f"/admin/users/{user['id']}/unban")

        self.assertEqual(ban_response.status_code, 302)
        self.assertIsNotNone(active_ban)
        self.assertEqual(unban_response.status_code, 302)
        with site.app.app_context():
            ban = site.get_db().execute(
                "SELECT * FROM user_bans WHERE user_id = ? AND lifted_at IS NULL",
                (user["id"],),
            ).fetchone()
        self.assertIsNone(ban)

    def test_banned_user_sees_red_notice_and_cannot_open_games(self):
        user_id = self.login("badplayer@example.com", "Bad Player")
        with site.app.app_context():
            db = site.get_db()
            cur = db.execute(
                "INSERT INTO projects (title, description, tags, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Blocked Game", "", "", "1", "now"),
            )
            project_id = cur.lastrowid
            db.execute(
                "INSERT INTO user_bans (user_id, reason, created_at, expires_at) "
                "VALUES (?, ?, ?, NULL)",
                (user_id, "Breaking the rules", "now"),
            )
            db.commit()

        game_response = self.client.get(f"/project/{project_id}")
        banned_response = self.client.get("/banned")

        self.assertEqual(game_response.status_code, 302)
        self.assertIn("/banned", game_response.headers["Location"])
        self.assertEqual(banned_response.status_code, 200)
        self.assertIn(b"You are banned", banned_response.data)
        self.assertIn(b"Click here for more info", banned_response.data)
        self.assertIn(b"Breaking the rules", banned_response.data)

    def test_regular_admin_cannot_ban_or_demote_admins(self):
        mod_id = self.login("mod@example.com", "Mod")
        with site.app.app_context():
            site.get_db().execute("UPDATE users SET is_admin = 1 WHERE id = ?", (mod_id,))
            other_admin = site.upsert_user("otheradmin@example.com", "Other Admin")
            site.get_db().execute("UPDATE users SET is_admin = 1 WHERE id = ?", (other_admin["id"],))
            site.get_db().commit()

        ban_response = self.client.post(
            f"/admin/users/{other_admin['id']}/ban",
            data={"duration": "forever", "reason": "Nope"},
        )
        demote_response = self.client.post(f"/admin/users/{other_admin['id']}/remove-admin")

        self.assertEqual(ban_response.status_code, 403)
        self.assertEqual(demote_response.status_code, 403)

    def test_owner_can_ban_and_demote_admins(self):
        self.login()
        with site.app.app_context():
            other_admin = site.upsert_user("otheradmin@example.com", "Other Admin")
            site.get_db().execute("UPDATE users SET is_admin = 1 WHERE id = ?", (other_admin["id"],))
            site.get_db().commit()

        ban_response = self.client.post(
            f"/admin/users/{other_admin['id']}/ban",
            data={"duration": "forever", "reason": "Owner action"},
        )
        unadmin_response = self.client.post(f"/admin/users/{other_admin['id']}/remove-admin")

        self.assertEqual(ban_response.status_code, 302)
        self.assertEqual(unadmin_response.status_code, 302)
        with site.app.app_context():
            user = site.get_db().execute(
                "SELECT is_admin FROM users WHERE id = ?",
                (other_admin["id"],),
            ).fetchone()
            ban = site.get_db().execute(
                "SELECT * FROM user_bans WHERE user_id = ? AND lifted_at IS NULL",
                (other_admin["id"],),
            ).fetchone()
        self.assertEqual(user["is_admin"], 0)
        self.assertIsNotNone(ban)

    def test_owner_can_change_user_password(self):
        self.login()
        with site.app.app_context():
            user = site.create_password_user("resetme", "oldsecret123", "resetme@example.com")

        response = self.client.post(
            f"/admin/users/{user['id']}/password",
            data={"password": "newsecret123"},
        )
        self.client.post("/logout")
        login_response = self.client.post(
            "/login",
            data={"email": "resetme", "password": "newsecret123"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(login_response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertIn("user_id", session)

    def test_regular_admin_cannot_change_user_password(self):
        mod_id = self.login("mod@example.com", "Mod")
        with site.app.app_context():
            db = site.get_db()
            db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (mod_id,))
            user = site.create_password_user("resetme", "oldsecret123", "resetme@example.com")
            db.commit()

        response = self.client.post(
            f"/admin/users/{user['id']}/password",
            data={"password": "newsecret123"},
        )

        self.assertEqual(response.status_code, 403)
        with site.app.app_context():
            stored = site.get_db().execute(
                "SELECT password_hash FROM users WHERE id = ?",
                (user["id"],),
            ).fetchone()
        self.assertTrue(check_password_hash(stored["password_hash"], "oldsecret123"))

    def test_owner_user_list_has_password_reset_without_showing_hashes(self):
        self.login()
        with site.app.app_context():
            user = site.create_password_user("resetme", "oldsecret123", "resetme@example.com")

        response = self.client.get("/admin")

        self.assertEqual(response.status_code, 200)
        self.assertIn(f"/admin/users/{user['id']}/password".encode(), response.data)
        self.assertIn(b"Password: set", response.data)
        self.assertNotIn(user["password_hash"].encode(), response.data)

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
