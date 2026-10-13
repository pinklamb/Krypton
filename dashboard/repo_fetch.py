"""Download one GitHub repository's source files into memory, without ever writing or running any of it.

The archive is read as a stream. A member becomes a source file only if it is a regular file with a
supported extension, a clean relative path and a size under the limits; links, devices, traversal
paths and dependency folders are dropped. Nothing is extracted to disk, so there is nothing to execute.

The token only goes to the GitHub API host. The archive itself is fetched from the redirect target,
which must be a GitHub download host, and without the token.
"""

from __future__ import annotations

import contextlib
import io
import re
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from pathlib import PurePosixPath

import auth

REPO_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
SOURCE_SUFFIXES = (".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
SKIP_DIRS = frozenset({".git", "node_modules", "vendor", "third_party", "dist", "build", "__pycache__", "site-packages", ".venv", "venv"})
DOWNLOAD_HOST = "codeload.github.com"
REDIRECTS = (301, 302, 303, 307, 308)
CHUNK = 64 * 1024


class ScanError(Exception):
    """A problem the visitor can be told about; `status` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


@dataclass(frozen=True)
class Limits:
    """Hard caps so one repository cannot use up the server."""

    download: int = 40 * 1024 * 1024  # compressed bytes
    unpacked: int = 300 * 1024 * 1024  # sum of member sizes
    files: int = 3000  # source files kept
    file_bytes: int = 1024 * 1024  # larger source files are skipped
    source_bytes: int = 20 * 1024 * 1024  # all kept source together
    seconds: float = 45.0  # whole download


def valid_repo(name: object) -> bool:
    """True for an `owner/name` string made only of GitHub's allowed characters."""
    return isinstance(name, str) and REPO_NAME.match(name) is not None and ".." not in name


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects so the caller can check where they lead."""

    def redirect_request(self, *args, **kwargs):
        return None


def _allowed_target(cfg: auth.Config, url: str) -> bool:
    """The archive may only come from GitHub's download host (or the API host itself, which tests use)."""
    target, api = urllib.parse.urlsplit(url), urllib.parse.urlsplit(cfg.api_base)
    if target.scheme == "https" and target.hostname in {DOWNLOAD_HOST, api.hostname}:
        return True
    return target.scheme == api.scheme == "http" and target.hostname == api.hostname and target.port == api.port


def _read_limited(reply, limit: int, deadline: float) -> bytes:
    """The whole body, or ScanError if it is too big or takes too long."""
    chunks, size = [], 0
    while chunk := reply.read(CHUNK):
        size += len(chunk)
        if size > limit:
            raise ScanError(413, "This repository is too large to analyze here.")
        if time.monotonic() > deadline:
            raise ScanError(504, "Downloading the repository took too long.")
        chunks.append(chunk)
    return b"".join(chunks)


def _open(request: urllib.request.Request, deadline: float):
    """Open a request without following redirects; the reply, or the redirect's HTTPError."""
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        return opener.open(request, timeout=max(1.0, min(15.0, deadline - time.monotonic())))
    except urllib.error.HTTPError as error:
        if error.code in REDIRECTS:
            return error
        problem = _http_problem(error)
        error.close()
        raise problem from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ScanError(502, "GitHub could not be reached.") from error


def _http_problem(error: urllib.error.HTTPError) -> ScanError:
    """Translate GitHub's error status into something to tell the visitor."""
    if error.code == 404:
        return ScanError(404, "That repository was not found, or this sign-in cannot see it.")
    if error.code in (401, 403):
        return ScanError(403, "GitHub refused access. Disconnect and connect again to approve repository access.")
    return ScanError(502, "GitHub could not provide that repository.")


def download_archive(cfg: auth.Config, token: str, full_name: str, limits: Limits = Limits()) -> bytes:
    """The repository's .tar.gz as bytes. The token is sent to the API host only."""
    deadline = time.monotonic() + limits.seconds
    api = urllib.request.Request(f"{cfg.api_base}/repos/{full_name}/tarball", headers={**auth.API_HEADERS, "Authorization": f"Bearer {token}"})
    with contextlib.closing(_open(api, deadline)) as first:
        location = first.headers.get("Location") if first.status in REDIRECTS else None
        if location is None:
            return _read_limited(first, limits.download, deadline)
    target = urllib.parse.urljoin(f"{cfg.api_base}/", location)
    if not _allowed_target(cfg, target):
        raise ScanError(502, "GitHub sent the archive from an unexpected address, so it was not downloaded.")
    with contextlib.closing(_open(urllib.request.Request(target, headers={"User-Agent": auth.API_HEADERS["User-Agent"]}), deadline)) as second:
        if second.status in REDIRECTS:
            raise ScanError(502, "GitHub sent the archive through an unexpected redirect.")
        return _read_limited(second, limits.download, deadline)


def _unsafe_member(member: tarfile.TarInfo) -> bool:
    """Anything that is not a plain regular file with a plain name: links, devices, absolute or odd paths."""
    return not member.isreg() or member.name.startswith("/") or "\\" in member.name or "\x00" in member.name


def _bad_part(part: str) -> bool:
    """A path segment that could climb out of the repository or is empty."""
    return part in ("..", "/", "") or part.startswith("/")


def _is_source(parts: tuple[str, ...]) -> bool:
    """A supported source file that is not inside a dependency or build folder."""
    return parts[-1].lower().endswith(SOURCE_SUFFIXES) and not any(part in SKIP_DIRS for part in parts[:-1])


def _clean_name(member: tarfile.TarInfo) -> str | None:
    """The repo-relative path of a regular source file in an allowed place, else None."""
    if _unsafe_member(member):
        return None
    parts = PurePosixPath(member.name).parts[1:]  # the first part is GitHub's `owner-repo-sha` folder
    return "/".join(parts) if parts and not any(map(_bad_part, parts)) and _is_source(parts) else None


class _Budget:
    """Running totals that stop an archive from using up the server."""

    def __init__(self, limits: Limits) -> None:
        self.limits, self.declared, self.kept, self.count = limits, 0, 0, 0

    def declare(self, size: int) -> None:
        """Count a member's declared size, whether or not it is kept."""
        self.declared += max(0, size)
        if self.declared > self.limits.unpacked:
            raise ScanError(413, "This repository is too large to analyze here.")

    def keep(self, size: int) -> None:
        """Count a source file that is being kept."""
        self.kept, self.count = self.kept + size, self.count + 1
        if self.count > self.limits.files or self.kept > self.limits.source_bytes:
            raise ScanError(413, "This repository has more source code than can be analyzed here.")


def extract_sources(archive: bytes, limits: Limits = Limits()) -> dict[str, str]:
    """{path: text} for the source files in a repository archive, read in memory and never written out."""
    sources, budget = {}, _Budget(limits)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r|gz") as tar:
            for member in tar:
                budget.declare(member.size)
                name = _clean_name(member)
                if name is not None and member.size <= limits.file_bytes:
                    payload = tar.extractfile(member).read(limits.file_bytes + 1)
                    budget.keep(len(payload))
                    sources[name] = payload.decode("utf-8", errors="replace")
    except (tarfile.TarError, EOFError, zlib.error, OSError) as error:
        raise ScanError(502, "GitHub sent an archive that could not be read.") from error
    return sources


def fetch_sources(cfg: auth.Config, token: str, full_name: str, limits: Limits = Limits()) -> dict[str, str]:
    """Download a repository and return its source files, or raise ScanError."""
    if not valid_repo(full_name):
        raise ScanError(400, "That is not a valid repository name.")
    return extract_sources(download_archive(cfg, token, full_name, limits), limits)
