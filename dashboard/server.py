"""Serve the Krypton site (landing, docs, the savings screen and "see your own tree") with GitHub sign-in, on $PORT, the way Railway expects.

Pages:    GET /  and every file of the site folder (SITE_DIR, default ../site)       GET /healthz
Sign-in:  GET /auth/github  /auth/github/callback    POST /auth/logout   (GitHub App, read-only contents; revoked at sign-out)
JSON:     GET /api/session  /api/repos
Analyze:  POST /api/scan {"repo": "owner/name"}   reads the repository in memory with the visitor's read-only access, builds its tree here,
          has it scored (the site's own KRYPTON_SERVICE_TOKEN), and answers numbers, the worst functions and a tree outline to draw

Nothing from the repository is written to disk, run or kept; the visitor's GitHub token is revoked at sign-out and forgotten after an hour.
"""

import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import auth
import scanner
from repo_fetch import ScanError, valid_repo

HERE = Path(__file__).parent
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")  # bar.svg, the lavender bar at the top of Krypton's pull request comment
HEADERS = {
    "Cache-Control": "public, max-age=300",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    # three.js from cdnjs, fonts from Google, GitHub avatars; the page may call this server and nothing else
    "Content-Security-Policy": "default-src 'none'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
                               "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; img-src 'self' data: https://avatars.githubusercontent.com; "
                               "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
}
NO_STORE = {**HEADERS, "Cache-Control": "no-store"}


def site_dir() -> Path:
    """Where the site's files are: SITE_DIR, else the repository's site folder next to this one."""
    return Path(os.environ.get("SITE_DIR") or HERE.parent / "site").resolve()


class Handler(BaseHTTPRequestHandler):
    """Routes requests; `cfg`, `sessions`, `gate` and `site` are bound by make_server."""

    cfg = auth.Config()
    sessions = auth.Sessions(cfg)
    gate = scanner.Gate()
    site = site_dir()

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

    # ---------------------------------------------------------------- the site's files
    def _inside(self, relative: str) -> Path | None:
        """The site file at `relative`, or None: nothing outside the site folder, no hidden files."""
        target = (self.site / relative).resolve()
        hidden = any(part.startswith(".") for part in Path(relative).parts)
        return None if hidden or self.site not in target.parents or not target.is_file() else target

    def _moved(self, location: str) -> None:
        """A permanent move to the clean address, keeping the query string."""
        query = urlsplit(self.path).query
        self.send_response(301)
        self.send_header("Location", location + (f"?{query}" if query else ""))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _file(self, path: str) -> None:
        """A file of the site folder, under clean addresses: `/` is index.html, `/docs` is docs.html, and an old `/docs.html` link moves to
        `/docs` (so the address bar never shows .html and old links keep working). Anything else is the site's own 404 page."""
        relative = path.lstrip("/")
        if relative.endswith(".html") and self._inside(relative):
            clean = relative[: -len("index.html")] if relative.endswith("index.html") else relative[: -len(".html")]
            return self._moved("/" + clean)
        if relative == "" or relative.endswith("/"):
            relative += "index.html"
        elif not Path(relative).suffix and self._inside(relative + ".html"):
            relative += ".html"
        target = self._inside(relative)
        if target is None:
            missing = self._inside("404.html")
            if missing is None:
                return self._text(404, "not found")
            return self._send(404, missing.read_bytes(), "text/html; charset=utf-8", with_body=self.command != "HEAD")
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if kind.startswith("text/") or kind in ("application/javascript", "application/json"):
            kind += "; charset=utf-8"
        self._send(200, target.read_bytes(), kind, with_body=self.command != "HEAD")

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
            return self._redirect("/tree?github=denied", done)
        verifier = auth.verifier_for(self.cfg, self._cookies(), (params.get("state") or [None])[0])
        if not verifier or "code" not in params:
            return self._text(400, "Sign-in could not be verified. Start again from the Connect GitHub button.", done)
        try:
            token = auth.exchange_code(self.cfg, params["code"][0], verifier)
            user = auth.public_user(auth.github_get(self.cfg, "/user", token))
        except auth.OAuthError as error:
            print(f"sign-in failed: {error}", flush=True)
            return self._redirect("/tree?github=error", done)
        self._redirect("/tree?github=connected", [self.sessions.create(user, token), *done])

    def _session(self) -> None:
        session = self.sessions.get(self._cookies())
        self._json({"configured": self.cfg.configured, "connected": bool(session), "user": session["user"] if session else None,
                    "install_url": auth.install_url(self.cfg)})

    def _repos(self) -> None:
        session = self.sessions.get(self._cookies())
        if not session:
            return self._json({"error": "not connected"}, 401)
        try:
            self._json(auth.granted_repos(self.cfg, session["token"]))
        except auth.OAuthError as error:
            print(f"listing repositories failed: {error}", flush=True)
            hint = " The site may be using an OAuth App's client id; it needs the GitHub App's (it starts with Iv)." if " 403 " in str(error) else ""
            self._json({"error": f"GitHub would not list your repositories.{hint}"}, 502)

    def _same_origin(self) -> bool:
        """State-changing requests must come from this site's own page (custom header, matching Origin)."""
        return self.headers.get("X-Requested-With") == "krypton" and self.headers.get("Origin", self.cfg.base_url) == self.cfg.base_url

    def _logout(self) -> None:
        if not self._same_origin():
            return self._text(403, "forbidden")
        session = self.sessions.delete(self._cookies())
        if session and self.cfg.configured:
            try:
                auth.revoke(self.cfg, session["token"])
            except auth.OAuthError as error:
                print(f"revoking a token failed: {error}", flush=True)
        headers = {**NO_STORE, "Clear-Site-Data": '"cookies"'}   # ask the browser to drop every cookie for this site, not only ours
        self._send(200, b"ok", "text/plain; charset=utf-8", headers, [auth.clear_cookie(self.cfg, auth.SESSION_COOKIE)], self.command != "HEAD")

    # ---------------------------------------------------------------- analysis
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
        else:
            self._file(path)

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


def make_server(cfg: auth.Config, port: int, host: str = "0.0.0.0", gate: scanner.Gate | None = None, site: Path | None = None) -> ThreadingHTTPServer:
    """A server whose handler uses `cfg`, its own session store, its own analysis limits and the given site folder."""
    bound = {"cfg": cfg, "sessions": auth.Sessions(cfg), "gate": gate or scanner.Gate(), "site": (site or site_dir()).resolve()}
    return ThreadingHTTPServer((host, port), type("BoundHandler", (Handler,), bound))


def main() -> None:
    """Start the server from the environment (Railway sets PORT)."""
    config = auth.load_config()
    scoring = bool(os.environ.get("KRYPTON_SCORE_URL", "").strip() and os.environ.get("KRYPTON_SERVICE_TOKEN", "").strip())
    print(announce(config, scoring), flush=True)
    port = int(os.environ.get("PORT", "8080"))
    print(f"serving {site_dir()} on 0.0.0.0:{port}", flush=True)
    make_server(config, port).serve_forever()


def announce(config: auth.Config, scoring: bool) -> str:
    """What the server says about sign-in, scoring and sessions at startup."""
    state = "on" if config.configured else "off (set GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET and BASE_URL)"
    score = "on" if scoring else "off (set KRYPTON_SCORE_URL and KRYPTON_SERVICE_TOKEN)"
    note = "" if os.environ.get("SESSION_SECRET") else " (SESSION_SECRET is not set, so sessions reset whenever the server restarts)"
    if config.configured and not config.app_slug:
        note += " (GITHUB_APP_SLUG is not set, so visitors get no link to choose which repositories the app may read)"
    return f"GitHub sign-in: {state}; scoring: {score}{note}"


if __name__ == "__main__":
    main()
