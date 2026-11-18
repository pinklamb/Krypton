"""GitHub sign-in for the site: a GitHub App's user authorization (code flow with PKCE), standard library only.

The GitHub App asks for Contents: read-only and Metadata: read-only, and each visitor chooses which repositories it may read, so the token a
visitor's tab receives can read code and nothing else. It is revoked at sign-out, and the server forgets it after an hour.

Settings come from the environment (see .env.example):
  GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET   the GitHub App's client id and a client secret
  GITHUB_APP_SLUG                           the app's name in its URL (github.com/apps/<slug>), for the "choose repositories" link
  BASE_URL                                  public address of this server, no trailing slash (the callback is BASE_URL/auth/github/callback)
  SESSION_SECRET                            random string that signs cookies; sessions reset on restart if it is unset
  GITHUB_SCOPES                             default "read:user repo"; use "read:user public_repo" to ask for less
  GITHUB_OAUTH_BASE, GITHUB_API_BASE        only for tests against a fake GitHub

The browser holds a signed session id; its own page may fetch the read-only token for this visit (/api/github-token) to read the chosen
repository straight from GitHub in the tab.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.cookies import SimpleCookie

SESSION_COOKIE, FLOW_COOKIE = "krypton_session", "krypton_oauth"
SESSION_TTL, FLOW_TTL, MAX_SESSIONS = 3600, 600, 500   # the sign-in cookie itself ends when the browser closes; the server also forgets a session after an hour
API_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "krypton-demo"}


class OAuthError(Exception):
    """Sign-in failed at GitHub (bad code, network, or a malformed reply)."""


@dataclass(frozen=True)
class Config:
    """Everything the flow needs; `configured` is False until the OAuth App's credentials are set."""

    client_id: str = ""
    client_secret: str = ""
    base_url: str = ""
    scopes: str = "read:user repo"
    secret: bytes = b""
    oauth_base: str = "https://github.com"
    api_base: str = "https://api.github.com"
    app_slug: str = ""

    @property
    def configured(self) -> bool:
        """True when sign-in can actually run."""
        return bool(self.client_id and self.client_secret and self.base_url)

    @property
    def secure(self) -> bool:
        """Cookies get the Secure flag whenever the public address is https."""
        return self.base_url.startswith("https://")

    @property
    def redirect_uri(self) -> str:
        """The callback address registered on the OAuth App."""
        return f"{self.base_url}/auth/github/callback"


def load_config(env=os.environ) -> Config:
    """Build the Config from environment variables."""
    secret = env.get("SESSION_SECRET") or secrets.token_urlsafe(32)
    return Config(
        client_id=env.get("GITHUB_CLIENT_ID", "").strip(),
        client_secret=env.get("GITHUB_CLIENT_SECRET", "").strip(),
        base_url=env.get("BASE_URL", "").strip().rstrip("/"),
        scopes=env.get("GITHUB_SCOPES", "read:user repo").strip(),
        secret=secret.encode(),
        oauth_base=env.get("GITHUB_OAUTH_BASE", "https://github.com").rstrip("/"),
        api_base=env.get("GITHUB_API_BASE", "https://api.github.com").rstrip("/"),
        app_slug=env.get("GITHUB_APP_SLUG", "").strip(),
    )


# ---------------------------------------------------------------- signed values and cookies
def sign(secret: bytes, value: str) -> str:
    """value.signature, signed with HMAC-SHA256."""
    mac = hmac.new(secret, value.encode(), hashlib.sha256).digest()
    return f"{value}.{base64.urlsafe_b64encode(mac).decode().rstrip('=')}"


def unsign(secret: bytes, signed: str | None) -> str | None:
    """The value inside a signed string, or None if it was altered."""
    value, _, _ = (signed or "").rpartition(".")
    return value if value and hmac.compare_digest(sign(secret, value), signed) else None


def parse_cookies(header: str | None) -> dict[str, str]:
    """Cookie name -> value from a Cookie header."""
    jar = SimpleCookie()
    jar.load(header or "")
    return {name: morsel.value for name, morsel in jar.items()}


def set_cookie(cfg: Config, name: str, value: str, max_age: int | None) -> str:
    """A Set-Cookie header value: HttpOnly, SameSite=Lax, Secure on https; no Max-Age (None) makes it a browser-session cookie."""
    age = [] if max_age is None else [f"Max-Age={max_age}"]
    flags = ["HttpOnly", "SameSite=Lax", "Path=/", *age] + (["Secure"] if cfg.secure else [])
    return f"{name}={value}; " + "; ".join(flags)


def clear_cookie(cfg: Config, name: str) -> str:
    """A Set-Cookie header value that deletes the cookie."""
    return set_cookie(cfg, name, "", 0)


# ---------------------------------------------------------------- the flow
def new_flow() -> tuple[str, str]:
    """A fresh (state, PKCE verifier) pair."""
    return secrets.token_urlsafe(32), secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    """PKCE S256 challenge for a verifier."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def authorize_url(cfg: Config, state: str, verifier: str) -> str:
    """Where to send the browser to sign in."""
    query = {"client_id": cfg.client_id, "redirect_uri": cfg.redirect_uri, "scope": cfg.scopes, "state": state, "code_challenge": code_challenge(verifier), "code_challenge_method": "S256", "prompt": "select_account"}
    return f"{cfg.oauth_base}/login/oauth/authorize?{urllib.parse.urlencode(query)}"


def flow_cookie(cfg: Config, state: str, verifier: str) -> str:
    """Set-Cookie value that remembers the state and verifier for the callback."""
    return set_cookie(cfg, FLOW_COOKIE, sign(cfg.secret, f"{state}:{verifier}"), FLOW_TTL)


def verifier_for(cfg: Config, cookies: dict[str, str], state: str | None) -> str | None:
    """The PKCE verifier if the callback's state matches the one we issued, else None."""
    saved = unsign(cfg.secret, cookies.get(FLOW_COOKIE))
    expected, _, verifier = (saved or "").partition(":")
    return verifier if saved and state and hmac.compare_digest(expected, state) else None


def _request(req: urllib.request.Request) -> dict:
    """Send a request to GitHub and parse the JSON reply."""
    try:
        with urllib.request.urlopen(req, timeout=10) as reply:
            return json.load(reply)
    except (urllib.error.URLError, ValueError, TimeoutError) as error:
        raise OAuthError(type(error).__name__) from error


def exchange_code(cfg: Config, code: str, verifier: str) -> str:
    """Trade the one-time code for an access token (server to server; the client secret never reaches the browser)."""
    body = urllib.parse.urlencode({"client_id": cfg.client_id, "client_secret": cfg.client_secret, "code": code, "redirect_uri": cfg.redirect_uri, "code_verifier": verifier}).encode()
    reply = _request(urllib.request.Request(f"{cfg.oauth_base}/login/oauth/access_token", data=body, headers={"Accept": "application/json", "User-Agent": "krypton-demo"}))
    if "access_token" not in reply:
        raise OAuthError(str(reply.get("error", "no_token")))
    return reply["access_token"]


def github_get(cfg: Config, path: str, token: str):
    """GET an API path with the user's token."""
    return _request(urllib.request.Request(f"{cfg.api_base}{path}", headers={**API_HEADERS, "Authorization": f"Bearer {token}"}))


def install_url(cfg: Config) -> str | None:
    """Where a visitor chooses which repositories the app may read."""
    return f"https://github.com/apps/{cfg.app_slug}/installations/new" if cfg.app_slug else None


def granted_repos(cfg: Config, token: str) -> list[dict]:
    """The repositories this visitor let the app read, across every installation they can see."""
    repos: list[dict] = []
    for installation in github_get(cfg, "/user/installations?per_page=100", token).get("installations", []):
        page = github_get(cfg, f"/user/installations/{int(installation['id'])}/repositories?per_page=100", token)
        repos.extend(page.get("repositories", []))
    return public_repos(sorted(repos, key=lambda r: r.get("pushed_at") or "", reverse=True))


def revoke(cfg: Config, token: str) -> None:
    """Tell GitHub the token is finished (sign-out): DELETE /applications/{client_id}/token with the app's own credentials."""
    basic = base64.b64encode(f"{cfg.client_id}:{cfg.client_secret}".encode()).decode()
    req = urllib.request.Request(f"{cfg.api_base}/applications/{cfg.client_id}/token", data=json.dumps({"access_token": token}).encode(), method="DELETE",
                                 headers={**API_HEADERS, "Authorization": f"Basic {basic}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except (urllib.error.URLError, TimeoutError) as error:
        raise OAuthError(type(error).__name__) from error


def public_user(raw: dict) -> dict:
    """The few profile fields the browser may see."""
    return {key: raw.get(key) for key in ("login", "name", "avatar_url")}


def public_repos(raw: list) -> list[dict]:
    """The few repository fields the browser may see."""
    return [{"full_name": r["full_name"], "private": bool(r.get("private")), "language": r.get("language"), "pushed_at": r.get("pushed_at")} for r in raw if "full_name" in r]


# ---------------------------------------------------------------- sessions (server memory)
class Sessions:
    """Signed-cookie sessions held in memory: id -> user and token, with an expiry and a size cap."""

    def __init__(self, cfg: Config) -> None:
        self.cfg, self._items, self._lock = cfg, {}, threading.Lock()

    def create(self, user: dict, token: str) -> str:
        """Store a session and return the Set-Cookie value for it."""
        sid = secrets.token_urlsafe(32)
        with self._lock:
            self._purge()
            self._items[sid] = {"user": user, "token": token, "expires": time.time() + SESSION_TTL}
        return set_cookie(self.cfg, SESSION_COOKIE, sign(self.cfg.secret, sid), None)

    def get(self, cookies: dict[str, str]) -> dict | None:
        """The live session for a request's cookies, or None."""
        sid = unsign(self.cfg.secret, cookies.get(SESSION_COOKIE))
        with self._lock:
            self._purge()   # an expired session's token is gone the moment anyone asks, not at the next sign-in
            return self._items.get(sid or "")

    def delete(self, cookies: dict[str, str]) -> dict | None:
        """Forget the request's session; the session that was forgotten, so its token can be revoked."""
        sid = unsign(self.cfg.secret, cookies.get(SESSION_COOKIE))
        with self._lock:
            return self._items.pop(sid or "", None)

    def _purge(self) -> None:
        """Drop expired sessions, and the oldest ones if the store is full (caller holds the lock)."""
        now = time.time()
        self._items = {k: v for k, v in self._items.items() if v["expires"] > now}
        for sid in sorted(self._items, key=lambda k: self._items[k]["expires"])[: max(0, len(self._items) - MAX_SESSIONS + 1)]:
            del self._items[sid]
