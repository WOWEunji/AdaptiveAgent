"""Docker sandbox backend 테스트.

#18 — opt-in Docker sandbox. CI 대부분에서 docker가 없으므로 실제 실행
테스트는 자동 skip. 정책·설정 분기는 항상 검증.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adaptive_agent.config import AgentConfig
from adaptive_agent.tools.registry import create_default_registry
from adaptive_agent.tools.sandbox import (
    DockerSandboxBackend,
    LocalSandboxBackend,
    SandboxPolicyViolation,
)


class DockerBackendConstructionTest(unittest.TestCase):
    def test_default_image_and_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            backend = DockerSandboxBackend(Path(tmp))
            self.assertEqual(backend.name, "docker")
            self.assertEqual(backend.image, "python:3.11-slim")
            self.assertEqual(backend.memory_limit, "256m")
            self.assertEqual(backend.network, "none")

    def test_custom_image_and_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            backend = DockerSandboxBackend(
                Path(tmp),
                image="python:3.12-alpine",
                memory_limit="128m",
                cpu_limit="0.5",
                pids_limit=64,
            )
            self.assertEqual(backend.image, "python:3.12-alpine")
            self.assertEqual(backend.memory_limit, "128m")
            self.assertEqual(backend.cpu_limit, "0.5")
            self.assertEqual(backend.pids_limit, 64)


class DockerBackendPolicyTest(unittest.TestCase):
    """정책 차단은 docker 없이도 동작 (subprocess 호출 전에 fire)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.backend = DockerSandboxBackend(self.workspace)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_policy_blocks_workspace_path(self) -> None:
        with self.assertRaises(SandboxPolicyViolation) as ctx:
            self.backend.run_python_code(
                f"open({str(self.workspace / 'leak.txt')!r}).read()",
                timeout_seconds=5.0,
            )
        self.assertEqual(ctx.exception.reason, "workspace_path")

    def test_policy_blocks_sensitive_absolute_path(self) -> None:
        with self.assertRaises(SandboxPolicyViolation) as ctx:
            self.backend.run_shell("cat /etc/passwd", shell_binary="/bin/sh", timeout_seconds=5.0)
        self.assertEqual(ctx.exception.reason, "sensitive_absolute_path")

    def test_policy_blocks_dangerous_shell_pattern(self) -> None:
        with self.assertRaises(SandboxPolicyViolation) as ctx:
            self.backend.run_shell("rm -rf x", shell_binary="/bin/sh", timeout_seconds=5.0)
        self.assertEqual(ctx.exception.reason, "dangerous_shell_pattern")


class RegistrySandboxBackendChoiceTest(unittest.TestCase):
    def test_default_uses_local_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = create_default_registry(Path(tmp))
            tool = registry.get("code_execute")
            self.assertIsNotNone(tool)
            # handler는 closure라 backend 직접 inspect는 어렵지만, registry
            # 생성이 LocalSandbox로 디폴트 처리되는지 indirect 검증.

    def test_invalid_backend_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                create_default_registry(Path(tmp), sandbox_backend="invalid")

    def test_docker_backend_rejected_when_unavailable(self) -> None:
        if DockerSandboxBackend.is_available():
            self.skipTest("Docker 사용 가능 — 가용성 검증 분기는 다른 환경에서 확인")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError) as ctx:
                create_default_registry(Path(tmp), sandbox_backend="docker")
            self.assertIn("docker", str(ctx.exception).lower())


@unittest.skipUnless(DockerSandboxBackend.is_available(), "docker CLI 사용 불가 — 통합 테스트 skip")
class DockerBackendExecutionTest(unittest.TestCase):
    """실제 docker run 검증 — Docker 데몬이 살아있을 때만 실행."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.backend = DockerSandboxBackend(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_run_python_returns_stdout(self) -> None:
        result = self.backend.run_python_code("print('hello docker')", timeout_seconds=30.0)
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("hello docker", result["stdout"])
        self.assertEqual(result["sandbox"]["backend"], "docker")

    def test_run_shell_uses_sh(self) -> None:
        result = self.backend.run_shell("echo from docker", shell_binary="/bin/sh", timeout_seconds=30.0)
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("from docker", result["stdout"])


class AgentConfigSandboxFieldsTest(unittest.TestCase):
    def test_default_is_local(self) -> None:
        config = AgentConfig()
        self.assertEqual(config.sandbox_backend, "local")
        self.assertEqual(config.sandbox_image, "python:3.11-slim")

    def test_explicit_docker_field_kept(self) -> None:
        config = AgentConfig(sandbox_backend="docker", sandbox_memory_limit="512m")
        self.assertEqual(config.sandbox_backend, "docker")
        self.assertEqual(config.sandbox_memory_limit, "512m")


if __name__ == "__main__":
    unittest.main()
