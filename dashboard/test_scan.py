"""Repository analysis tests against a fake GitHub: safe download and extraction, nothing executed, token handling, limits and the endpoint.

    python -m unittest test_scan        (from dashboard/)

Scoring needs the Rust tools: KRYPTON_BIN (krypton) and a krypton-server answering at KRYPTON_SCORE_URL (default http://127.0.0.1:8081). The tests that score are skipped when the server is not up.
"""

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).parent))
import auth  # noqa: E402
import scanner  # noqa: E402
import server  # noqa: E402
from fake_github import TOKEN, FakeGitHub, archive  # noqa: E402
from repo_fetch import Limits, ScanError, extract_sources, fetch_sources, valid_repo  # noqa: E402
from test_oauth import Browser, serve  # noqa: E402

def scoring_is_up() -> bool:
    """Are the Rust tools the worker needs here: krypton, and a scoring server answering where the worker will look for it?"""
    import shutil
    import urllib.request

    if shutil.which(os.environ.get("KRYPTON_BIN", "krypton")) is None:
        return False
    try:
        urllib.request.urlopen(os.environ.get("KRYPTON_SCORE_URL", "http://127.0.0.1:8081") + "/v1/health", timeout=2)
    except OSError:
        return False
    return True


needs_scoring = unittest.skipUnless(scoring_is_up(), "krypton and a scoring server are not available (see the module docstring)")

REPO = "octotest/krypton-test-codebase"
JSON_HEADERS = {"X-Requested-With": "krypton", "Content-Type": "application/json"}


class Visitor(Browser):
    """A cookie-keeping client that can also POST a JSON body."""

    def post(self, path, payload, headers=JSON_HEADERS):
        body = json.dumps(payload).encode()
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("POST", path, body=body, headers={**self._cookie_header(), **headers})
        try:
            reply = conn.getresponse()
            text = reply.read().decode()
        finally:
            conn.close()
        self._remember(reply)
        return reply, text


class GitHubCase(unittest.TestCase):
    """A fake GitHub plus helpers; subclasses add the demo server when they need it."""

    @classmethod
    def setUpClass(cls):
        cls.github = serve(FakeGitHub)
        cls.base = f"http://127.0.0.1:{cls.github.server_port}"
        cls.cfg = auth.Config(client_id="cid", client_secret="shh", base_url="http://127.0.0.1:0", secret=b"test-secret", oauth_base=cls.base, api_base=cls.base)

    @classmethod
    def tearDownClass(cls):
        cls.github.shutdown()
        cls.github.server_close()

    def setUp(self):
        FakeGitHub.redirect_to, FakeGitHub.big_archive, FakeGitHub.marker, FakeGitHub.download_auth = "", 0, "", []


class FetchTests(GitHubCase):
    # Extract keeps only clean source files.
    def test_extract_sources(self):
        names = set(extract_sources(archive()))
        self.assertEqual(names, {"setup.py", "src/app.py", "src/ui.js"})

    # Repository names are checked.
    def test_repo_names(self):
        for good in ("octotest/garth", "a-b/c_d.e"):
            self.assertTrue(valid_repo(good), good)
        for bad in ("../x", "a/b/c", "a b/c", "a/b;ls", "owner/", "/name", "a/..", "", None, 7):
            self.assertFalse(valid_repo(bad), bad)

    # Download works and the token goes only to the api host.
    def test_download_token(self):
        sources = fetch_sources(self.cfg, TOKEN, REPO)
        self.assertIn("src/app.py", sources)
        self.assertEqual(FakeGitHub.download_auth, [None])

    # An untrusted redirect is refused.
    def test_untrusted_redirect(self):
        FakeGitHub.redirect_to = "http://evil.invalid/steal"
        with self.assertRaises(ScanError) as caught:
            fetch_sources(self.cfg, TOKEN, REPO)
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(FakeGitHub.download_auth, [])

    # Unknown repository is a 404.
    def test_repo_404(self):
        with self.assertRaises(ScanError) as caught:
            fetch_sources(self.cfg, TOKEN, "octotest/not-there")
        self.assertEqual(caught.exception.status, 404)

    # Wrong token is a 403.
    def test_token_403(self):
        with self.assertRaises(ScanError) as caught:
            fetch_sources(self.cfg, "gho_WRONG", REPO)
        self.assertEqual(caught.exception.status, 403)

    # Size limits stop the download and the unpacking.
    def test_size_limits(self):
        with self.assertRaises(ScanError) as caught:
            fetch_sources(self.cfg, TOKEN, REPO, Limits(download=200))
        self.assertEqual(caught.exception.status, 413)
        with self.assertRaises(ScanError) as caught:
            extract_sources(archive(), Limits(files=1))
        self.assertEqual(caught.exception.status, 413)
        with self.assertRaises(ScanError) as caught:
            extract_sources(archive(), Limits(unpacked=50))
        self.assertEqual(caught.exception.status, 413)

    # Garbage is not an archive.
    def test_garbage_archive(self):
        FakeGitHub.big_archive = 5000
        with self.assertRaises(ScanError) as caught:
            fetch_sources(self.cfg, TOKEN, REPO)
        self.assertEqual(caught.exception.status, 502)

    # Nothing in the repository is ever run.
    @needs_scoring
    def test_nothing_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder, "executed.marker")
            FakeGitHub.marker = str(marker)
            result = scanner.analyze(self.cfg, TOKEN, REPO)
            self.assertEqual(result["files"], 3)  # setup.py was scored like any file
            self.assertFalse(marker.exists())


class EndpointTests(GitHubCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = server.make_server(cls.cfg, 0, "127.0.0.1", scanner.Gate(cooldown=0))
        handler = cls.app.RequestHandlerClass
        handler.cfg = auth.Config(**{**cls.cfg.__dict__, "base_url": f"http://127.0.0.1:{cls.app.server_port}"})
        handler.sessions = auth.Sessions(handler.cfg)
        threading.Thread(target=cls.app.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.app.shutdown()
        cls.app.server_close()
        super().tearDownClass()

    def connected(self, code="scan-code", port=None):
        visitor = Visitor(port or self.app.server_port)
        reply, _ = visitor.call("GET", "/auth/github")
        query = parse_qs(urlsplit(reply.getheader("Location")).query)
        FakeGitHub.challenges[code] = query["code_challenge"][0]
        visitor.call("GET", f"/auth/github/callback?code={code}&state={query['state'][0]}")
        return visitor

    # Scan needs a connected visitor and the header.
    def test_scan_auth(self):
        stranger = Visitor(self.app.server_port)
        self.assertEqual(stranger.post("/api/scan", {"repo": REPO})[0].status, 401)
        me = self.connected("c1")
        self.assertEqual(me.post("/api/scan", {"repo": REPO}, headers={"Content-Type": "application/json"})[0].status, 403)

    # Bad names and bodies are 400.
    def test_bad_bodies(self):
        me = self.connected("c2")
        for payload in ({"repo": "../etc/passwd"}, {"repo": 5}, {}, {"repo": "a/b/c"}):
            self.assertEqual(me.post("/api/scan", payload)[0].status, 400, payload)

    @needs_scoring
    def test_scan_returns_numbers_only(self):
        me = self.connected("c3")
        reply, body = me.post("/api/scan", {"repo": REPO})
        self.assertEqual(reply.status, 200, body)
        data = json.loads(body)
        self.assertEqual((data["repo"], data["files"], data["tangled"]), (REPO, 3, 1))
        self.assertEqual(data["worst"][0]["file"], "src/app.py")
        self.assertGreater(data["worst"][0]["times"], 1)
        self.assertGreater(data["tokens"], 0)
        for secret in ("gho_", "out.append", "def tangled", "private@example.com"):
            self.assertNotIn(secret, body)

    # Unknown repository is reported.
    def test_unknown_repo(self):
        me = self.connected("c4")
        reply, body = me.post("/api/scan", {"repo": "octotest/not-there"})
        self.assertEqual(reply.status, 404)
        self.assertIn("not found", json.loads(body)["error"].lower())

    # Visitors wait between analyses.
    @needs_scoring
    def test_visitor_cooldown(self):
        slow = server.make_server(self.cfg, 0, "127.0.0.1", scanner.Gate(cooldown=60))
        handler = slow.RequestHandlerClass
        handler.cfg = auth.Config(**{**self.cfg.__dict__, "base_url": f"http://127.0.0.1:{slow.server_port}"})
        handler.sessions = auth.Sessions(handler.cfg)
        threading.Thread(target=slow.serve_forever, daemon=True).start()
        try:
            me = self.connected("c5", slow.server_port)
            self.assertEqual(me.post("/api/scan", {"repo": REPO})[0].status, 200)
            reply, body = me.post("/api/scan", {"repo": REPO})
            self.assertEqual(reply.status, 429)
            self.assertIn("wait", json.loads(body)["error"].lower())
        finally:
            slow.shutdown()
            slow.server_close()


class GateTests(unittest.TestCase):
    # Only a few run at once.
    def test_parallel_limit(self):
        gate = scanner.Gate(parallel=1, cooldown=0)
        gate.start("a")
        with self.assertRaises(ScanError) as caught:
            gate.start("b")
        self.assertEqual(caught.exception.status, 429)
        gate.finish()
        gate.start("b")
        gate.finish()


def _tar(files: dict[str, str]) -> bytes:
    """A GitHub-style archive: everything under one `owner-repo-sha` folder."""
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"owner-repo-abc123/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


NL = chr(10)
TANGLED = "def tangled(x):" + NL + "".join("    " * (i + 1) + f"if x > {i}:" + NL for i in range(12)) + "    " * 13 + "return x" + NL
BOOTSTRAP = NL.join(["/*!", " * Bootstrap v5.3.2", " * Licensed under MIT", " */"] + [f"function f{i}(a) {{ if (a) {{ return {i}; }} return 0; }}" for i in range(60)]) + NL


@needs_scoring
class BundledLibraryTests(GitHubCase):
    # Bundled libraries are left out and reported.
    def test_bundled_libraries(self):
        files = {"src/app.py": TANGLED, "public/js/bootstrap.js": BOOTSTRAP, "legacy/old.py": TANGLED, ".gitattributes": "legacy/ linguist-vendored" + NL}
        sources = extract_sources(_tar(files))
        self.assertIn(".gitattributes", sources)
        data = scanner.run_worker(sources)
        self.assertEqual(data["files"], 1)                                   # only src/app.py is scored
        self.assertEqual(data["tangled"], 1)
        self.assertEqual(data["excluded"]["count"], 2)
        self.assertEqual(set(data["excluded"]["examples"]), {"public/js/bootstrap.js", "legacy/old.py"})

    # A function with a waiver comment is not counted as tangled.
    def test_waiver_comment(self):
        data = scanner.run_worker(extract_sources(_tar({"src/app.py": "# krypton: ignore -- legacy" + NL + TANGLED})))
        self.assertEqual(data["tangled"], 0)

    # A repository without libraries reports nothing left out.
    def test_no_libraries(self):
        data = scanner.run_worker(extract_sources(_tar({"src/app.py": TANGLED})))
        self.assertEqual(data["excluded"], {"count": 0, "examples": []})


if __name__ == "__main__":
    unittest.main()


class OutlineTests(unittest.TestCase):
    """The tree the page draws keeps every tangled function whole, however much of the rest is trimmed."""

    # A tangled node past the trimmed part is kept with its ancestors, and the renumbered parents stay valid.
    def test_kept_nodes(self):
        import scan_worker
        parent = [-1, 0, 1, 1, 0, 4, 5, 6]          # 0 > 1 > (2, 3);  0 > 4 > 5 > 6 > 7
        marks = ["0"] * 7 + ["1"]                   # only the last node belongs to a tangled function
        keep = scan_worker.kept_nodes(parent, marks, 0.25)
        self.assertEqual(keep, [0, 1, 4, 5, 6, 7], "the first quarter, then the tangled node and its whole branch")
        renumber = {old: new for new, old in enumerate(keep)}
        self.assertTrue(all(renumber[parent[old]] < new for new, old in enumerate(keep) if new), "every parent comes before its child")
