"""Sign-in tests against a fake GitHub: the real flow end to end (state, PKCE, token exchange, sessions, logout), no network and no credentials.

    python -m unittest dashboard.test_oauth        (from the repo root)   or   python -m unittest test_oauth   (from dashboard/)
"""

import contextlib
import http.client
import io
import json
import sys
import threading
import unittest
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
        reply = conn.getresponse()
        body = reply.read().decode()
        self._remember(reply)
        return reply, body


class SignInTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.github = serve(FakeGitHub)
        base = f"http://127.0.0.1:{cls.github.server_port}"
        cls.cfg = auth.Config(client_id="cid", client_secret="shh", base_url="http://127.0.0.1:0", secret=b"test-secret", oauth_base=base, api_base=base)
        cls.app = server.make_server(cls.cfg, 0, "127.0.0.1")
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

    def test_login_redirects_with_state_and_pkce(self):
        reply, query = self.start_login()
        self.assertEqual(reply.status, 302)
        self.assertEqual(query["client_id"], ["cid"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["scope"], ["read:user repo"])
        cookie = reply.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_full_flow_connects_and_never_leaks_the_token(self):
        reply, _ = self.finish_login()
        self.assertEqual((reply.status, reply.getheader("Location")), (302, "/?github=connected"))
        _, body = self.me.call("GET", "/api/session")
        data = json.loads(body)
        self.assertTrue(data["connected"])
        self.assertEqual(data["user"], {"login": "octotest", "name": "Octo Test", "avatar_url": data["user"]["avatar_url"]})
        _, repos = self.me.call("GET", "/api/repos")
        self.assertEqual(json.loads(repos), [{"full_name": r["full_name"], "private": r["private"], "language": "Python", "pushed_at": r["pushed_at"]} for r in REPOS])
        for text in (body, repos):
            self.assertNotIn("gho_", text)
            self.assertNotIn("private@example.com", text)

    def test_wrong_or_missing_state_is_refused(self):
        self.start_login()
        reply, _ = self.me.call("GET", "/auth/github/callback?code=x&state=not-the-state")
        self.assertEqual(reply.status, 400)
        stranger = Browser(self.app.server_port)
        reply, _ = stranger.call("GET", "/auth/github/callback?code=x&state=anything")
        self.assertEqual(reply.status, 400)

    def test_wrong_verifier_fails_at_github(self):
        _, query = self.start_login()
        FakeGitHub.challenges["c"] = "a-different-challenge"
        reply, _ = self.me.call("GET", f"/auth/github/callback?code=c&state={query['state'][0]}")
        self.assertEqual(reply.getheader("Location"), "/?github=error")
        self.assertFalse(json.loads(self.me.call("GET", "/api/session")[1])["connected"])

    def test_denied_and_failed_exchange_redirect_with_a_reason(self):
        reply, _ = self.me.call("GET", "/auth/github/callback?error=access_denied")
        self.assertEqual(reply.getheader("Location"), "/?github=denied")
        FakeGitHub.fail_exchange = True
        reply, _ = self.finish_login("code-2")
        self.assertEqual(reply.getheader("Location"), "/?github=error")

    def test_logout_needs_the_header_and_ends_the_session(self):
        self.finish_login()
        reply, _ = self.me.call("POST", "/auth/logout")
        self.assertEqual(reply.status, 403)
        self.assertTrue(json.loads(self.me.call("GET", "/api/session")[1])["connected"])
        reply, _ = self.me.call("POST", "/auth/logout", {"X-Requested-With": "argon"})
        self.assertEqual(reply.status, 200)
        self.assertFalse(json.loads(self.me.call("GET", "/api/session")[1])["connected"])
        self.assertEqual(self.me.call("GET", "/api/repos")[0].status, 401)

    def test_tampered_cookie_is_not_a_session(self):
        self.finish_login()
        name = auth.SESSION_COOKIE
        self.me.cookies[name] = self.me.cookies[name][:-3] + "AAA"
        self.assertFalse(json.loads(self.me.call("GET", "/api/session")[1])["connected"])

    def test_logs_do_not_contain_codes_or_state(self):
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            self.finish_login("secret-code-xyz")
        self.assertNotIn("secret-code-xyz", log.getvalue())
        self.assertNotIn("state=", log.getvalue())

    def test_unconfigured_server_says_so(self):
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

    def test_pages_still_served_with_connect_src(self):
        reply, body = self.me.call("GET", "/")
        self.assertEqual(reply.status, 200)
        self.assertIn("connect-src 'self'", reply.getheader("Content-Security-Policy"))
        self.assertEqual(self.me.call("GET", "/benchmarks")[0].status, 200)
        self.assertEqual(self.me.call("GET", "/nope")[0].status, 404)


if __name__ == "__main__":
    unittest.main()
