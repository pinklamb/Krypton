"""GitHub sign-in for the demo: OAuth authorization code flow with PKCE, standard library only.

Settings come from the environment (see .env.example):
  GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET   the OAuth App's credentials
  BASE_URL                                  public address of this server, no trailing slash (the callback is BASE_URL/auth/github/callback)
  SESSION_SECRET                            random string that signs cookies; sessions reset on restart if it is unset
  GITHUB_SCOPES                             default "read:user repo"; use "read:user public_repo" to ask for less
  GITHUB_OAUTH_BASE, GITHUB_API_BASE        only for tests against a fake GitHub

The access token stays on the server. The browser only holds a signed session id.
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
SESSION_TTL, FLOW_TTL, MAX_SESSIONS = 8 * 3600, 600, 500
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


def set_cookie(cfg: Config, name: str, value: str, max_age: int) -> str:
    """A Set-Cookie header value: HttpOnly, SameSite=Lax, Secure on https."""
    flags = ["HttpOnly", "SameSite=Lax", "Path=/", f"Max-Age={max_age}"] + (["Secure"] if cfg.secure else [])
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
    query = {"client_id": cfg.client_id, "redirect_uri": cfg.redirect_uri, "scope": cfg.scopes, "state": state, "code_challenge": code_challenge(verifier), "code_challenge_method": "S256"}
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
        return set_cookie(self.cfg, SESSION_COOKIE, sign(self.cfg.secret, sid), SESSION_TTL)

    def get(self, cookies: dict[str, str]) -> dict | None:
        """The live session for a request's cookies, or None."""
        sid = unsign(self.cfg.secret, cookies.get(SESSION_COOKIE))
        with self._lock:
            item = self._items.get(sid or "")
            return item if item and item["expires"] > time.time() else None

    def delete(self, cookies: dict[str, str]) -> None:
        """Forget the request's session."""
        sid = unsign(self.cfg.secret, cookies.get(SESSION_COOKIE))
        with self._lock:
            self._items.pop(sid or "", None)

    def _purge(self) -> None:
        """Drop expired sessions, and the oldest ones if the store is full (caller holds the lock)."""
        now = time.time()
        self._items = {k: v for k, v in self._items.items() if v["expires"] > now}
        for sid in sorted(self._items, key=lambda k: self._items[k]["expires"])[: max(0, len(self._items) - MAX_SESSIONS + 1)]:
            del self._items[sid]
