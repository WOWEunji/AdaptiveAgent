"""artifact_store + web_fetch 내장 도구 테스트.

#19 — 신규 builtin 두 종류:
- artifact_store: put/get/list/delete + sha256 ID + 크기/개수 한도
- web_fetch: 화이트리스트 도메인만 허용, max_bytes/timeout 기본값
"""

from __future__ import annotations

import base64
import json
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from adaptive_agent.tools import builtins
from adaptive_agent.tools.registry import create_default_registry


class ArtifactStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _put(self, **kwargs):
        return builtins.artifact_store({"op": "put", **kwargs}, workspace=self.workspace)

    def test_put_then_get_round_trip_text(self) -> None:
        put = self._put(name="hello.txt", content="안녕 world", mime_type="text/plain")
        self.assertTrue(put.success, put.error)
        sha = put.output["artifact_id"]

        got = builtins.artifact_store({"op": "get", "artifact_id": sha}, workspace=self.workspace)
        self.assertTrue(got.success, got.error)
        decoded = base64.b64decode(got.output["content_base64"]).decode("utf-8")
        self.assertEqual(decoded, "안녕 world")
        self.assertEqual(got.output["mime_type"], "text/plain")

    def test_put_with_base64_content(self) -> None:
        binary = bytes(range(256))
        encoded = base64.b64encode(binary).decode("ascii")
        put = self._put(name="bin.dat", content_base64=encoded, mime_type="application/octet-stream")
        self.assertTrue(put.success, put.error)

        got = builtins.artifact_store({"op": "get", "artifact_id": put.output["artifact_id"]}, workspace=self.workspace)
        self.assertTrue(got.success)
        self.assertEqual(base64.b64decode(got.output["content_base64"]), binary)

    def test_list_returns_stored_entries(self) -> None:
        self._put(name="a.txt", content="hi a")
        self._put(name="b.txt", content="hi b")
        self._put(name="c.bin", content="hi c")

        listed = builtins.artifact_store({"op": "list"}, workspace=self.workspace)
        self.assertTrue(listed.success)
        names = {entry["name"] for entry in listed.output["entries"]}
        self.assertEqual(names, {"a.txt", "b.txt", "c.bin"})

    def test_list_with_prefix_filters(self) -> None:
        self._put(name="prod-001.log", content="prod-001-content")
        self._put(name="prod-002.log", content="prod-002-content")
        self._put(name="dev-001.log", content="dev-001-content")
        listed = builtins.artifact_store({"op": "list", "prefix": "prod-"}, workspace=self.workspace)

        self.assertTrue(listed.success)
        names = {entry["name"] for entry in listed.output["entries"]}
        self.assertEqual(names, {"prod-001.log", "prod-002.log"})

    def test_delete_removes_artifact(self) -> None:
        put = self._put(name="ephemeral.txt", content="bye")
        sha = put.output["artifact_id"]
        artifacts_dir = self.workspace / ".adaptive_agent" / "artifacts"
        self.assertTrue((artifacts_dir / f"{sha}.bin").exists())

        deleted = builtins.artifact_store({"op": "delete", "artifact_id": sha}, workspace=self.workspace)
        self.assertTrue(deleted.success)
        self.assertFalse((artifacts_dir / f"{sha}.bin").exists())

    def test_max_bytes_blocks_oversized_payload(self) -> None:
        result = builtins.artifact_store(
            {"op": "put", "name": "big.bin", "content": "x" * 200},
            workspace=self.workspace,
            max_bytes=100,
        )
        self.assertFalse(result.success)
        self.assertIn("한도", result.error)

    def test_max_count_blocks_when_full(self) -> None:
        for i in range(2):
            r = builtins.artifact_store(
                {"op": "put", "name": f"x{i}.txt", "content": str(i)},
                workspace=self.workspace,
                max_count=2,
            )
            self.assertTrue(r.success)
        full = builtins.artifact_store(
            {"op": "put", "name": "overflow.txt", "content": "no"},
            workspace=self.workspace,
            max_count=2,
        )
        self.assertFalse(full.success)
        self.assertIn("개수 한도", full.error)

    def test_invalid_op_is_rejected(self) -> None:
        result = builtins.artifact_store({"op": "evil"}, workspace=self.workspace)
        self.assertFalse(result.success)
        self.assertIn("지원하지 않는 op", result.error)

    def test_get_with_invalid_artifact_id_is_rejected(self) -> None:
        result = builtins.artifact_store({"op": "get", "artifact_id": "not-hex"}, workspace=self.workspace)
        self.assertFalse(result.success)
        self.assertIn("64자 hex", result.error)

    def test_name_with_path_traversal_is_rejected(self) -> None:
        for bad in ("../escape", "sub/dir.txt", ".."):
            with self.subTest(name=bad):
                result = self._put(name=bad, content="x")
                self.assertFalse(result.success)
                self.assertIn("디렉터리", result.error)

    def test_artifact_store_is_registered_in_default_registry(self) -> None:
        registry = create_default_registry(self.workspace)
        tool = registry.get("artifact_store")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.category, "filesystem")


class WebFetchPolicyTest(unittest.TestCase):
    def test_blocks_when_allowlist_empty(self) -> None:
        result = builtins.web_fetch(
            {"url": "https://example.com"},
            allowed_domains=[],
        )
        self.assertFalse(result.success)
        verdict = result.output["verdict"]
        self.assertTrue(verdict["policy_blocked"])
        self.assertEqual(verdict["block_reason"], "domain_not_allowlisted")
        self.assertEqual(verdict["host"], "example.com")

    def test_blocks_unlisted_domain(self) -> None:
        result = builtins.web_fetch(
            {"url": "https://malicious.example.com"},
            allowed_domains=["api.openai.com"],
        )
        self.assertFalse(result.success)
        self.assertEqual(result.output["verdict"]["block_reason"], "domain_not_allowlisted")

    def test_allowed_subdomain_passes_policy_check(self) -> None:
        # 실제 fetch는 안 되더라도(네트워크 없음) 정책 단계는 통과해야 함.
        # 여기서는 host 매칭 로직만 검증한다.
        from adaptive_agent.tools.builtins import _domain_in_allowlist

        self.assertTrue(_domain_in_allowlist("api.openai.com", ["openai.com"]))
        self.assertTrue(_domain_in_allowlist("openai.com", ["openai.com"]))
        self.assertFalse(_domain_in_allowlist("evil-openai.com", ["openai.com"]))
        self.assertFalse(_domain_in_allowlist("openai.com.evil", ["openai.com"]))

    def test_unsupported_scheme_is_rejected(self) -> None:
        result = builtins.web_fetch(
            {"url": "file:///etc/passwd"},
            allowed_domains=["openai.com"],
        )
        self.assertFalse(result.success)
        self.assertIn("scheme", result.error)

    def test_url_required(self) -> None:
        result = builtins.web_fetch({}, allowed_domains=["x"])
        self.assertFalse(result.success)
        self.assertIn("url", result.error)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/ok":
            body = b"hello world"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/big":
            body = b"x" * 5_000
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404, "not found")

    def log_message(self, *args, **kwargs):  # silence test logs
        return


class WebFetchHTTPTest(unittest.TestCase):
    """Loopback HTTP server로 실제 fetch까지 검증."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_get_succeeds_when_loopback_allowed(self) -> None:
        result = builtins.web_fetch(
            {"url": f"http://127.0.0.1:{self.port}/ok"},
            allowed_domains=["127.0.0.1"],
            timeout_seconds=2.0,
        )
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.output["status_code"], 200)
        self.assertIn("hello world", result.output["body_text"])
        self.assertFalse(result.output["body_truncated"])

    def test_max_bytes_truncates_body(self) -> None:
        result = builtins.web_fetch(
            {"url": f"http://127.0.0.1:{self.port}/big"},
            allowed_domains=["127.0.0.1"],
            max_bytes=1000,
            timeout_seconds=2.0,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.output["bytes_read"], 1000)
        self.assertTrue(result.output["body_truncated"])

    def test_404_returns_failure_with_status_code(self) -> None:
        result = builtins.web_fetch(
            {"url": f"http://127.0.0.1:{self.port}/missing"},
            allowed_domains=["127.0.0.1"],
            timeout_seconds=2.0,
        )
        self.assertFalse(result.success)
        self.assertEqual(result.output["status_code"], 404)


if __name__ == "__main__":
    unittest.main()
