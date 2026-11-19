"""Sign-in tests against a fake GitHub: the real flow end to end (state, PKCE, token exchange, sessions, logout), no network and no credentials.

    python -m unittest dashboard.test_oauth        (from the repo root)   or   python -m unittest test_oauth   (from dashboard/)
"""

import contextlib
import http.client
import io
import json
import sys
import threading
import time
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).parent))
import auth  # noqa: E402
import server  # noqa: E402
from fake_github import REPOS, FakeGitHub  # noqa: E402

def serve(handler, host="127.0.0.1"):
    srv = ThreadingHTTPServer((host, 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Browser:
    """A tiny cookie-keeping client for the demo server."""

    def __init__(self, port):
        self.port, self.cookies = port, {}

    def _cookie_header(self):
        return {"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items())} if self.cookies else {}

    def _remember(self, reply):
        for header in reply.headers.get_all("Set-Cookie") or []:
            name, _, rest = header.partition("=")
            value = rest.split(";")[0]
            if value:
                self.cookies[name] = value
            else:
                self.cookies.pop(name, None)

    def call(self, method, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request(method, path, headers={**self._cookie_header(), **(headers or {})})
        try:
            reply = conn.getresponse()
            body = reply.read().decode()
        finally:
            conn.close()
        self._remember(reply)
        return reply, body


class SignInTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.github = serve(FakeGitHub)
        base = f"http://127.0.0.1:{cls.github.server_port}"
        cls.cfg = auth.Config(client_id="cid", client_secret="shh", base_url="http://127.0.0.1:0", secret=b"test-secret", oauth_base=base, api_base=base)
        cls.app =server.make_server(cls.cfg, 0, "127.0.0.1")
        cls.app.RequestHandlerClass.cfg = auth.Config(**{**cls.cfg.__dict__, "base_url": f"http://127.0.0.1:{cls.app.server_port}"})
        cls.app.RequestHandlerClass.sessions = auth.Sessions(cls.app.RequestHandlerClass.cfg)
        threading.Thread(target=cls.app.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for srv in (cls.app, cls.github):
            srv.shutdown()
            srv.server_close()

    def setUp(self):
        FakeGitHub.fail_exchange, FakeGitHub.challenges = False, {}
        self.me = Browser(self.app.server_port)

    def start_login(self):
        reply, _ = self.me.call("GET", "/auth/github")
        query = parse_qs(urlsplit(reply.getheader("Location")).query)
        return reply, query

    def finish_login(self, code="code-1"):
        _, query = self.start_login()
        FakeGitHub.challenges[code] = query["code_challenge"][0]
        return self.me.call("GET", f"/auth/github/callback?code={code}&state={query['state'][0]}")

    # Login redirects with state and pkce.
    def test_login_redirect(self):
        reply, query = self.start_login()
        self.assertEqual(reply.status, 302)
        self.assertEqual(query["client_id"], ["cid"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["scope"], ["read:user repo"])
        self.assertEqual(query["prompt"], ["select_account"])                       # GitHub shows its account chooser instead of silently reusing the last sign-in
        cookie = reply.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    # Full flow connects and never leaks the token.
    def test_sign_in_flow(self):
        reply, _ = self.finish_login()
        self.assertEqual((reply.status, reply.getheader("Location")), (302, "/tree?github=connected"))
        _, body = self.me.call("GET", "/api/session")
        data = json.loads(body)
        self.assertTrue(data["connected"])
        self.assertEqual(data["user"], {"login": "octotest", "name": "Octo Test", "avatar_url": data["user"]["avatar_url"]})
        _, repos = self.me.call("GET", "/api/repos")
        self.assertEqual(json.loads(repos), [{"full_name": r["full_name"], "private": r["private"], "language": "Python", "pushed_at": r["pushed_at"]} for r in REPOS])
        for text in (body, repos):
            self.assertNotIn("gho_", text)
            self.assertNotIn("private@example.com", text)

    # Wrong or missing state is refused.
    def test_bad_state(self):
        self.start_login()
        reply, _ = self.me.call("GET", "/auth/github/callback?code=x&state=not-the-state")
        self.assertEqual(reply.status, 400)
        stranger = Browser(self.app.server_port)
        reply, _ = stranger.call("GET", "/auth/github/callback?code=x&state=anything")
        self.assertEqual(reply.status, 400)

    # Wrong verifier fails at github.
    def test_bad_verifier(self):
        _, query = self.start_login()
        FakeGitHub.challenges["c"] = "a-different-challenge"
        reply, _ = self.me.call("GET", f"/auth/github/callback?code=c&state={query['state'][0]}")
        self.assertEqual(reply.getheader("Location"), "/tree?github=error")
        self.assertFalse(json.loads(self.me.call("GET", "/api/session")[1])["connected"])

    # Denied and failed exchange redirect with a reason.
    def test_denied_exchange(self):
        reply, _ = self.me.call("GET", "/auth/github/callback?error=access_denied")
        self.assertEqual(reply.getheader("Location"), "/tree?github=denied")
        FakeGitHub.fail_exchange = True
        reply, _ = self.finish_login("code-2")
        self.assertEqual(reply.getheader("Location"), "/tree?github=error")

    # The sign in cookie ends with the browser session.
    def test_session_cookie(self):
        reply, _ = self.finish_login()
        session = [c for c in reply.headers.get_all("Set-Cookie") if c.startswith(auth.SESSION_COOKIE + "=") and not c.startswith(auth.SESSION_COOKIE + "=;")][0]
        self.assertNotIn("Max-Age", session)
        self.assertNotIn("Expires", session)
        self.assertIn("HttpOnly", session)

    # Logging out clears the cookies.
    def test_logout_cookies(self):
        self.finish_login()
        reply, _ = self.me.call("POST", "/auth/logout", {"X-Requested-With": "krypton"})
        self.assertEqual(reply.getheader("Clear-Site-Data"), '"cookies"')
        self.assertIn("Max-Age=0", reply.getheader("Set-Cookie"))
        self.assertNotIn(auth.SESSION_COOKIE, self.me.cookies)

    # Logout needs the header and ends the session.
    def test_logout_header(self):
        self.finish_login()
        reply, _ = self.me.call("POST", "/auth/logout")
        self.assertEqual(reply.status, 403)
        self.assertTrue(json.loads(self.me.call("GET", "/api/session")[1])["connected"])
        reply, _ = self.me.call("POST", "/auth/logout", {"X-Requested-With": "krypton"})
        self.assertEqual(reply.status, 200)
        self.assertFalse(json.loads(self.me.call("GET", "/api/session")[1])["connected"])
        self.assertEqual(self.me.call("GET", "/api/repos")[0].status, 401)

    # Tampered cookie is not a session.
    def test_tampered_cookie(self):
        self.finish_login()
        name = auth.SESSION_COOKIE
        self.me.cookies[name] = self.me.cookies[name][:-3] + "AAA"
        self.assertFalse(json.loads(self.me.call("GET", "/api/session")[1])["connected"])

    # Logs do not contain codes or state.
    def test_log_secrets(self):
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            self.finish_login("secret-code-xyz")
        self.assertNotIn("secret-code-xyz", log.getvalue())
        self.assertNotIn("state=", log.getvalue())

    # Unconfigured server says so.
    def test_unconfigured(self):
        off = server.make_server(auth.Config(), 0, "127.0.0.1")
        threading.Thread(target=off.serve_forever, daemon=True).start()
        try:
            visitor = Browser(off.server_port)
            self.assertEqual(visitor.call("GET", "/auth/github")[0].status, 503)
            data = json.loads(visitor.call("GET", "/api/session")[1])
            self.assertEqual((data["configured"], data["connected"]), (False, False))
        finally:
            off.shutdown()
            off.server_close()

    # The site's files are served with the policy; nothing outside the site folder or hidden is.
    def test_site_files(self):
        reply, body = self.me.call("GET", "/")
        self.assertEqual(reply.status, 200)
        self.assertIn("connect-src 'self';", reply.getheader("Content-Security-Policy"))
        docs, page = self.me.call("GET", "/docs")
        self.assertEqual((docs.status, docs.getheader("Content-Type")), (200, "text/html; charset=utf-8"), "a clean address serves the page")
        self.assertIn("How Krypton works", page)
        css, _ = self.me.call("GET", "/assets/site.css")
        self.assertTrue(css.getheader("Content-Type").startswith("text/css"))
        for missing in ("/nope", "/../dashboard/auth.py", "/%2e%2e/dashboard/auth.py", "/.git/config"):
            self.assertEqual(self.me.call("GET", missing)[0].status, 404, missing)

    # Old .html links move to the clean address, keeping the query; an unknown page gets the site's own 404 page.
    def test_clean_addresses(self):
        for old, clean in (("/docs.html", "/docs"), ("/index.html", "/"), ("/tree.html?repo=a/b&sha=1", "/tree?repo=a/b&sha=1")):
            reply, _ = self.me.call("GET", old)
            self.assertEqual((reply.status, reply.getheader("Location")), (301, clean), old)
        reply, page = self.me.call("GET", "/no-such-page")
        self.assertEqual(reply.status, 404)
        self.assertIn("<html", page.lower(), "the site's 404 page, not bare text")


    # Signing out revokes the token with GitHub.
    def test_logout_revokes(self):
        FakeGitHub.revoked = []
        self.finish_login()
        self.me.call("POST", "/auth/logout", {"X-Requested-With": "krypton"})
        self.assertEqual(FakeGitHub.revoked, ["gho_FAKE_TOKEN"])


class SessionLifetimeTests(unittest.TestCase):
    """The GitHub token is kept only for the session: gone on logout, and gone on expiry even if nobody signs in again."""

    def setUp(self):
        self.cfg = auth.Config(client_id="cid", client_secret="shh", base_url="http://127.0.0.1:0", secret=b"test-secret")
        self.sessions = auth.Sessions(self.cfg)

    def cookies(self, token="gho_secret"):
        cookie = self.sessions.create({"login": "octo"}, token).split(";")[0]
        name, _, value = cookie.partition("=")
        return {name: value}

    # Logout forgets the token.
    def test_logout_token(self):
        cookies = self.cookies()
        self.sessions.delete(cookies)
        self.assertIsNone(self.sessions.get(cookies))
        self.assertNotIn("gho_secret", repr(self.sessions._items))

    # An expired session is dropped when it is next asked for.
    def test_session_expiry(self):
        cookies = self.cookies()
        self.assertIsNotNone(self.sessions.get(cookies))
        with unittest.mock.patch("auth.time.time", return_value=time.time() + auth.SESSION_TTL + 1):
            self.assertIsNone(self.sessions.get(cookies))
        self.assertNotIn("gho_secret", repr(self.sessions._items))


if __name__ == "__main__":
    unittest.main()
