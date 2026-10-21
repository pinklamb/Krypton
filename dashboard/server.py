"""Serve the Krypton demo (index.html), the full benchmark data (benchmarks.html) and GitHub sign-in on $PORT, the way Railway expects.

Pages:    GET /  /benchmarks  /healthz
Sign-in:  GET /auth/github  /auth/github/callback    POST /auth/logout
JSON:     GET /api/session  /api/repos               (the access token never leaves the server)
Analyze:  POST /api/scan {"repo": "owner/name"}      (reads the repository in memory and scores it; runs none of its code)
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import auth
import scanner
from repo_fetch import ScanError, valid_repo

HERE = Path(__file__).parent
PAGES = {name: (HERE / name).read_bytes() for name in ("index.html", "benchmarks.html")}
ROUTES = {"/": "index.html", "/index.html": "index.html", "/benchmarks": "benchmarks.html", "/benchmarks.html": "benchmarks.html"}
HEADERS = {
    "Cache-Control": "public, max-age=300",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    # self-contained apart from Google Fonts and GitHub avatars; fetch() may only call this server
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
                               "script-src 'unsafe-inline'; img-src data: https://avatars.githubusercontent.com; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
}
NO_STORE = {**HEADERS, "Cache-Control": "no-store"}


class Handler(BaseHTTPRequestHandler):
    """Routes requests; `cfg` and `sessions` are bound by make_server."""

    cfg = auth.Config()
    sessions = auth.Sessions(cfg)
    gate = scanner.Gate()

    # ---------------------------------------------------------------- plumbing
    def _send(self, status: int, body: bytes, kind: str, headers=HEADERS, cookies=(), with_body=True) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers.items():
            self.send_header(name, value)
        for cookie in cookies:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def _text(self, status: int, text: str, cookies=()) -> None:
        self._send(status, text.encode(), "text/plain; charset=utf-8", NO_STORE, cookies, self.command != "HEAD")

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data).encode(), "application/json", NO_STORE, (), self.command != "HEAD")

    def _redirect(self, location: str, cookies=()) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        for name, value in NO_STORE.items():
            self.send_header(name, value)
        for cookie in cookies:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _cookies(self) -> dict:
        return auth.parse_cookies(self.headers.get("Cookie"))

    def _page(self, name: str) -> None:
        self._send(200, PAGES[name], "text/html; charset=utf-8", with_body=self.command != "HEAD")

    # ---------------------------------------------------------------- sign-in
    def _login(self) -> None:
        if not self.cfg.configured:
            return self._text(503, "GitHub sign-in is not configured on this server.")
        state, verifier = auth.new_flow()
        self._redirect(auth.authorize_url(self.cfg, state, verifier), [auth.flow_cookie(self.cfg, state, verifier)])

    def _callback(self) -> None:
        params = parse_qs(urlsplit(self.path).query)
        done = [auth.clear_cookie(self.cfg, auth.FLOW_COOKIE)]
        if "error" in params:
            return self._redirect("/?github=denied", done)
        verifier = auth.verifier_for(self.cfg, self._cookies(), (params.get("state") or [None])[0])
        if not verifier or "code" not in params:
            return self._text(400, "Sign-in could not be verified. Start again from the Connect GitHub button.", done)
        try:
            token = auth.exchange_code(self.cfg, params["code"][0], verifier)
            user = auth.public_user(auth.github_get(self.cfg, "/user", token))
        except auth.OAuthError as error:
            print(f"sign-in failed: {error}", flush=True)
            return self._redirect("/?github=error", done)
        self._redirect("/?github=connected", [self.sessions.create(user, token), *done])

    def _session(self) -> None:
        session = self.sessions.get(self._cookies())
        self._json({"available": True, "configured": self.cfg.configured, "connected": bool(session), "user": session["user"] if session else None})

    def _repos(self) -> None:
        session = self.sessions.get(self._cookies())
        if not session:
            return self._json({"error": "not connected"}, 401)
        try:
            raw = auth.github_get(self.cfg, "/user/repos?per_page=40&sort=pushed&affiliation=owner,collaborator,organization_member", session["token"])
        except auth.OAuthError:
            return self._json({"error": "GitHub did not answer"}, 502)
        self._json(auth.public_repos(raw))

    def _same_origin(self) -> bool:
        """State-changing requests must come from this site's own page (custom header, matching Origin)."""
        return self.headers.get("X-Requested-With") == "krypton" and self.headers.get("Origin", self.cfg.base_url) == self.cfg.base_url

    def _logout(self) -> None:
        if not self._same_origin():
            return self._text(403, "forbidden")
        self.sessions.delete(self._cookies())
        headers = {**NO_STORE, "Clear-Site-Data": '"cookies"'}   # ask the browser to drop every cookie for this site, not only ours
        self._send(200, b"ok", "text/plain; charset=utf-8", headers, [auth.clear_cookie(self.cfg, auth.SESSION_COOKIE)], self.command != "HEAD")

    def _repo_from_body(self) -> str | None:
        """The `repo` field of a small JSON body, or None if it is missing or malformed."""
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= 2048:
            return None
        try:
            repo = json.loads(self.rfile.read(length)).get("repo")
        except (ValueError, AttributeError):
            return None
        return repo if valid_repo(repo) else None

    def _scan(self) -> None:
        session = self.sessions.get(self._cookies())
        if not self._same_origin():
            return self._json({"error": "forbidden"}, 403)
        if not session:
            return self._json({"error": "Connect GitHub first."}, 401)
        repo = self._repo_from_body()
        if repo is None:
            return self._json({"error": "That is not a valid repository name."}, 400)
        try:
            self.gate.start(session["user"]["login"])
        except ScanError as error:
            return self._json({"error": error.message}, error.status)
        try:
            self._json(scanner.analyze(self.cfg, session["token"], repo))
        except ScanError as error:
            self._json({"error": error.message}, error.status)
        finally:
            self.gate.finish()

    # ---------------------------------------------------------------- routing
    def _get_routes(self) -> dict:
        return {"/healthz": lambda: self._text(200, "ok"), "/api/session": self._session, "/api/repos": self._repos,
                "/auth/github": self._login, "/auth/github/callback": self._callback}

    def _route(self) -> None:
        path = urlsplit(self.path).path
        action = self._get_routes().get(path)
        if action:
            action()
        elif path in ROUTES:
            self._page(ROUTES[path])
        else:
            self._text(404, "not found")

    def do_GET(self) -> None:
        self._route()

    def do_HEAD(self) -> None:
        self._route()

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/auth/logout":
            self._logout()
        elif path == "/api/scan":
            self._scan()
        else:
            self._text(404, "not found")

    def log_request(self, code="-", size="-") -> None:
        """Log the path without its query string, so one-time sign-in codes never reach the logs."""
        print(f"{self.address_string()} {self.command} {urlsplit(self.path).path} {code}", flush=True)

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} {fmt % args}", flush=True)


def make_server(cfg: auth.Config, port: int, host: str = "0.0.0.0", gate: scanner.Gate | None = None) -> ThreadingHTTPServer:
    """A server whose handler uses `cfg`, its own session store and its own analysis limits."""
    handler = type("BoundHandler", (Handler,), {"cfg": cfg, "sessions": auth.Sessions(cfg), "gate": gate or scanner.Gate()})
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    """Start the server from the environment (Railway sets PORT)."""
    config = auth.load_config()
    print(announce(config), flush=True)
    port = int(os.environ.get("PORT", "8080"))
    print(f"serving the dashboard on 0.0.0.0:{port}", flush=True)
    make_server(config, port).serve_forever()


def announce(config: auth.Config) -> str:
    """What the server says about sign-in and sessions at startup."""
    state = "on" if config.configured else "off (set GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET and BASE_URL)"
    note = "" if os.environ.get("SESSION_SECRET") else " (SESSION_SECRET is not set, so sessions reset whenever the server restarts)"
    return f"GitHub sign-in: {state}{note}"


if __name__ == "__main__":
    main()
