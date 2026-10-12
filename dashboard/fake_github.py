"""A stand-in for GitHub's OAuth and API, for tests and for trying the sign-in flow locally with no credentials.

    python fake_github.py 9001
    GITHUB_CLIENT_ID=cid GITHUB_CLIENT_SECRET=shh BASE_URL=http://127.0.0.1:8080 \\
    GITHUB_OAUTH_BASE=http://127.0.0.1:9001 GITHUB_API_BASE=http://127.0.0.1:9001 python server.py

It approves every sign-in, checks the PKCE verifier the way GitHub does, and serves one fake user with two repositories.
"""

import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import auth

AVATAR = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64'><circle cx='32' cy='32' r='32' fill='%236a4bd8'/></svg>"
USER = {"login": "octotest", "name": "Octo Test", "avatar_url": AVATAR, "email": "private@example.com"}
REPOS = [
    {"full_name": "octotest/argon-test-codebase", "private": True, "language": "Python", "pushed_at": "2026-10-10T00:00:00Z", "token_hint": "never_shown"},
    {"full_name": "octotest/garth", "private": False, "language": "Python", "pushed_at": "2026-09-01T00:00:00Z"},
]
TOKEN = "gho_FAKE_TOKEN"


class FakeGitHub(BaseHTTPRequestHandler):
    """Authorize (auto-approve), exchange a code for a token (PKCE-checked), then /user and /user/repos."""

    challenges: dict = {}
    fail_exchange = False
    secret = "shh"

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorize(self, query):
        code = secrets.token_urlsafe(8)
        self.challenges[code] = query["code_challenge"][0]
        self.send_response(302)
        self.send_header("Location", f"{query['redirect_uri'][0]}?{urlencode({'code': code, 'state': query['state'][0]})}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        code, verifier = form["code"][0], form["code_verifier"][0]
        bad = self.fail_exchange or self.challenges.get(code) != auth.code_challenge(verifier) or form["client_secret"][0] != self.secret
        self._json({"error": "bad_verification_code"} if bad else {"access_token": TOKEN, "token_type": "bearer", "scope": "read:user"})

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/login/oauth/authorize":
            return self._authorize(parse_qs(url.query))
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._json({"message": "Bad credentials"}, 401)
        self._json(USER if url.path == "/user" else REPOS)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9001
    print(f"fake GitHub on http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), FakeGitHub).serve_forever()
