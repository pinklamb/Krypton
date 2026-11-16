"""A stand-in for GitHub's OAuth and API, for tests and for trying the sign-in flow locally with no credentials.

    python fake_github.py 9001
    GITHUB_CLIENT_ID=cid GITHUB_CLIENT_SECRET=shh BASE_URL=http://127.0.0.1:8080 \\
    GITHUB_OAUTH_BASE=http://127.0.0.1:9001 GITHUB_API_BASE=http://127.0.0.1:9001 python server.py

It approves every sign-in, checks the PKCE verifier the way GitHub does, serves one fake user with two repositories,
and hands out a repository archive (including hostile members) through a redirect, like GitHub's tarball endpoint.
"""

import io
import json
import secrets
import sys
import tarfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import auth

AVATAR = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64'><circle cx='32' cy='32' r='32' fill='%236a4bd8'/></svg>"
USER = {"login": "octotest", "name": "Octo Test", "avatar_url": AVATAR, "email": "private@example.com"}
REPOS = [
    {"full_name": "octotest/krypton-test-codebase", "private": True, "language": "Python", "pushed_at": "2026-10-10T00:00:00Z", "token_hint": "never_shown"},
    {"full_name": "octotest/garth", "private": False, "language": "Python", "pushed_at": "2026-09-01T00:00:00Z"},
]
TOKEN = "gho_FAKE_TOKEN"
KNOWN = {r["full_name"] for r in REPOS}


def tangled(depth: int = 4) -> str:
    """A deeply nested function the engine rates over its threshold."""
    lines, indent = ["def tangled(items, flag):", "    out = []"], "    "
    for level in range(depth):
        lines += [f"{indent}for x{level} in items:", f"{indent}    if x{level} and flag > {level}:"]
        indent += "        "
    return "\n".join(lines + [f"{indent}out.append(x0)", "    return out", ""])


def archive(marker: str = "") -> bytes:
    """A .tar.gz shaped like GitHub's: one top folder, a few good files and several hostile members."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        def add(name, data=b"", kind=tarfile.REGTYPE, link=""):
            info = tarfile.TarInfo(name)
            info.type, info.size, info.linkname = kind, len(data) if kind == tarfile.REGTYPE else 0, link
            tar.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
        add("octotest-repo-abc123/src/app.py", tangled().encode() + b"\ndef fine(x):\n    return x + 1\n")
        add("octotest-repo-abc123/src/ui.js", b"export function hello(name) {\n  return 'hi ' + name;\n}\n")
        add("octotest-repo-abc123/setup.py", f"open({marker!r}, 'w').write('executed')\n".encode())  # parsed only, never run
        add("octotest-repo-abc123/README.md", b"# not source\n")
        add("octotest-repo-abc123/node_modules/dep/index.js", b"module.exports = 1;\n")
        add("octotest-repo-abc123/../escape.py", b"print('traversal')\n")
        add("/absolute.py", b"print('absolute')\n")
        add("octotest-repo-abc123/src/link.py", kind=tarfile.SYMTYPE, link="/etc/passwd")
    return buffer.getvalue()


class FakeGitHub(BaseHTTPRequestHandler):
    """Authorize (auto-approve), exchange a code for a token (PKCE-checked), then /user and /user/repos."""

    challenges: dict = {}
    fail_exchange = False
    secret = "shh"
    redirect_to = ""  # set to send the archive redirect somewhere else
    download_auth: list = []  # Authorization header seen on each archive download
    marker = ""  # a path the hostile setup.py would write if anything ever ran it
    big_archive = 0  # when set, serve this many bytes of junk instead of an archive

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

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _download(self):
        self.download_auth.append(self.headers.get("Authorization"))
        body = b"x" * self.big_archive if self.big_archive else archive(self.marker)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _tarball(self, repo):
        if repo not in KNOWN:
            return self._json({"message": "Not Found"}, 404)
        self._redirect(self.redirect_to or f"http://{self.headers['Host']}/codeload/{repo}")

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/login/oauth/authorize":
            return self._authorize(parse_qs(url.query))
        if url.path.startswith("/codeload/"):
            return self._download()
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._json({"message": "Bad credentials"}, 401)
        if url.path.startswith("/repos/") and url.path.endswith("/tarball"):
            return self._tarball(url.path[len("/repos/") : -len("/tarball")])
        self._json(USER if url.path == "/user" else REPOS)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9001
    print(f"fake GitHub on http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), FakeGitHub).serve_forever()
