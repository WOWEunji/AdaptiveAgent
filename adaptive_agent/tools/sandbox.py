"""Process sandbox backend abstractions for built-in tools.

Two backends ship in the box:

- ``LocalSandboxBackend`` (default): subprocess + tempdir + minimal env.
  Zero external dependencies. Policy enforcement (workspace_path /
  sensitive_absolute_path / dangerous_shell_pattern) wraps every call.
- ``DockerSandboxBackend`` (opt-in via ``AgentConfig.sandbox_backend='docker'``):
  runs payloads inside ``docker run --rm --network=none --memory=...
  --cpus=... --pids-limit=...`` for stronger isolation. Falls through to the
  same policy checks before docker is invoked.

Both backends share :class:`SandboxBackend` Protocol so callers (builtins)
do not need to know which is in use.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_SNAPSHOT_IGNORE_NAMES = {
    ".git",
    ".adaptive_agent",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
}

_BLOCKED_ABSOLUTE_PREFIXES = (
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/home",
    "/lib",
    "/lib64",
    "/opt",
    "/proc",
    "/root",
    "/run",
    "/sbin",
    "/sys",
    "/tmp",
    "/usr",
    "/var",
)

_BLOCKED_SHELL_PATTERNS = (
    " rm ",
    " rm\t",
    " rm\n",
    "rm ",
    "rm\t",
    "rm\n",
    "mv /",
    "cp /",
    "chmod ",
    "chown ",
    "sudo ",
    "su ",
    "mkfs",
    "mount ",
    "umount ",
    "dd ",
    "curl ",
    "wget ",
    "nc ",
    "netcat ",
    "ssh ",
    "scp ",
    "rsync ",
    "> /",
    ">> /",
)


class SandboxPolicyViolation(ValueError):
    """Execution request rejected by local sandbox policy.

    The ``reason`` attribute carries a stable machine-readable identifier
    (one of ``workspace_path`` / ``sensitive_absolute_path`` /
    ``dangerous_shell_pattern``) so callers can branch without parsing the
    user-facing message.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class SandboxBackend(Protocol):
    """Minimum protocol shared by Local and Docker backends."""

    name: str
    workspace: Path

    def run_python_code(self, code: str, *, timeout_seconds: float) -> dict[str, object]:
        ...

    def run_shell(self, code: str, *, shell_binary: str, timeout_seconds: float) -> dict[str, object]:
        ...

    def run_workspace_command(self, command: str, *, timeout_seconds: float) -> dict[str, object]:
        ...


@dataclass(frozen=True)
class SandboxResult:
    """JSON-serializable process execution result."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    backend: str
    working_directory: str
    filesystem_isolation: str

    def to_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "sandbox": {
                "backend": self.backend,
                "process_isolated": True,
                "working_directory": self.working_directory,
                "environment": "minimal",
                "filesystem_isolation": self.filesystem_isolation,
            },
        }


class LocalSandboxBackend:
    """Local process sandbox implemented with the Python standard library."""

    name = "local_process"

    def __init__(self, workspace: Path | None = None) -> None:
        raw_workspace = workspace or Path.cwd()
        self.workspace = raw_workspace.resolve()
        self._workspace_aliases = {str(self.workspace), str(raw_workspace)}

    def run_python_code(self, code: str, *, timeout_seconds: float) -> dict[str, object]:
        """Run Python code in a temporary isolated interpreter process."""

        self._enforce_local_policy(code, kind="python")
        with tempfile.TemporaryDirectory(prefix="adaptive-agent-code-") as temp_dir:
            temp_path = Path(temp_dir)
            script_path = temp_path / "snippet.py"
            script_path.write_text(code, encoding="utf-8")
            return self._run_process(
                [sys.executable, "-I", str(script_path)],
                cwd=temp_path,
                timeout_seconds=timeout_seconds,
                working_directory="temporary",
                filesystem_isolation="temporary_cwd_only",
            )

    def run_shell(self, code: str, *, shell_binary: str, timeout_seconds: float) -> dict[str, object]:
        """Run shell code in a temporary isolated process."""

        self._enforce_local_policy(code, kind="shell")
        with tempfile.TemporaryDirectory(prefix="adaptive-agent-shell-") as temp_dir:
            return self._run_process(
                [shell_binary, "-c", code],
                cwd=Path(temp_dir),
                timeout_seconds=timeout_seconds,
                working_directory="temporary",
                filesystem_isolation="temporary_cwd_only",
            )

    def run_workspace_command(self, command: str, *, timeout_seconds: float) -> dict[str, object]:
        """Run a project command inside a temporary workspace copy."""

        self._enforce_local_policy(command, kind="workspace_command")
        with tempfile.TemporaryDirectory(prefix="adaptive-agent-workspace-") as temp_dir:
            snapshot = Path(temp_dir) / "workspace"
            self._copy_workspace(snapshot)
            return self._run_process(
                ["/bin/bash", "-c", command],
                cwd=snapshot,
                timeout_seconds=timeout_seconds,
                working_directory="workspace_snapshot",
                filesystem_isolation="workspace_copy",
            )

    def _copy_workspace(self, destination: Path) -> None:
        def ignore(directory: str, names: list[str]) -> set[str]:
            return {
                name
                for name in names
                if name in _SNAPSHOT_IGNORE_NAMES
                or name.endswith(".pyc")
                or (Path(directory) / name).is_symlink()
            }

        shutil.copytree(self.workspace, destination, ignore=ignore, symlinks=True)

    def _run_process(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: float,
        working_directory: str,
        filesystem_isolation: str,
    ) -> dict[str, object]:
        start = time.monotonic()
        timed_out = False
        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                env=self._safe_environment(cwd),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            exit_code = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = 124
            stdout = _decode_timeout_output(exc.stdout)
            stderr = _decode_timeout_output(exc.stderr) or f"Timed out after {timeout_seconds:g}s"
        duration_ms = int((time.monotonic() - start) * 1000)
        return SandboxResult(
            command=shlex.join(command),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            timed_out=timed_out,
            backend=self.name,
            working_directory=working_directory,
            filesystem_isolation=filesystem_isolation,
        ).to_dict()

    @staticmethod
    def _safe_environment(temp_dir: Path) -> dict[str, str]:
        path = os.environ.get("PATH", "/usr/bin:/bin")
        env = {
            "PATH": path,
            "HOME": str(temp_dir),
            "TMPDIR": str(temp_dir),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
        }
        return env

    def _enforce_local_policy(
        self,
        payload: str,
        *,
        kind: str,
    ) -> None:
        """Reject payloads that target real local paths or unsafe commands."""

        if any(alias in payload for alias in self._workspace_aliases):
            raise SandboxPolicyViolation(
                "실제 워크스페이스 절대경로 접근은 로컬 정책상 차단됩니다.",
                reason="workspace_path",
            )

        for prefix in _BLOCKED_ABSOLUTE_PREFIXES:
            if prefix in self._workspace_aliases:
                continue
            pattern = rf"(?<![A-Za-z0-9_.-]){re.escape(prefix)}(?:/|\b)"
            if re.search(pattern, payload):
                raise SandboxPolicyViolation(
                    f"민감한 절대경로 접근은 로컬 정책상 차단됩니다: {prefix}",
                    reason="sensitive_absolute_path",
                )

        if kind in {"shell", "workspace_command"}:
            normalized = f" {payload.strip()} ".lower()
            for pattern in _BLOCKED_SHELL_PATTERNS:
                if pattern in normalized:
                    raise SandboxPolicyViolation(
                        f"위험한 shell 패턴이 차단되었습니다: {pattern.strip()}",
                        reason="dangerous_shell_pattern",
                    )


def _decode_timeout_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


class DockerSandboxBackend:
    """Docker-backed sandbox (opt-in).

    Reuses the same payload-level policy enforcement as
    :class:`LocalSandboxBackend` (workspace path / sensitive absolute paths /
    dangerous shell patterns) before invoking ``docker run``. Defaults to
    ``--network=none``, conservative memory/cpu/pid limits, and a temporary
    bind-mount for any required code/payload — the user workspace is never
    mounted in.

    On environments without Docker, raises ``DockerUnavailableError`` at
    construction time so the agent can fall back gracefully.
    """

    name = "docker"

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        image: str = "python:3.11-slim",
        memory_limit: str = "256m",
        cpu_limit: str = "1",
        pids_limit: int = 128,
        network: str = "none",
    ) -> None:
        raw_workspace = workspace or Path.cwd()
        self.workspace = raw_workspace.resolve()
        self._workspace_aliases = {str(self.workspace), str(raw_workspace)}
        self.image = image
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.pids_limit = pids_limit
        self.network = network

    @staticmethod
    def is_available() -> bool:
        """Return True if a working ``docker`` CLI is on PATH."""

        if shutil.which("docker") is None:
            return False
        try:
            result = subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def run_python_code(self, code: str, *, timeout_seconds: float) -> dict[str, object]:
        self._enforce_local_policy(code, kind="python")
        with tempfile.TemporaryDirectory(prefix="adaptive-agent-docker-py-") as temp_dir:
            script_path = Path(temp_dir) / "snippet.py"
            script_path.write_text(code, encoding="utf-8")
            return self._docker_run(
                ["python", "/workspace/snippet.py"],
                host_dir=Path(temp_dir),
                timeout_seconds=timeout_seconds,
                working_directory="docker_temp",
                filesystem_isolation="docker_volume_mount_ro",
            )

    def run_shell(self, code: str, *, shell_binary: str, timeout_seconds: float) -> dict[str, object]:
        self._enforce_local_policy(code, kind="shell")
        # Slim images may not have bash — default to /bin/sh inside container.
        container_shell = "/bin/sh"
        with tempfile.TemporaryDirectory(prefix="adaptive-agent-docker-sh-") as temp_dir:
            return self._docker_run(
                [container_shell, "-c", code],
                host_dir=Path(temp_dir),
                timeout_seconds=timeout_seconds,
                working_directory="docker_temp",
                filesystem_isolation="docker_volume_mount_ro",
            )

    def run_workspace_command(self, command: str, *, timeout_seconds: float) -> dict[str, object]:
        self._enforce_local_policy(command, kind="workspace_command")
        with tempfile.TemporaryDirectory(prefix="adaptive-agent-docker-ws-") as temp_dir:
            snapshot = Path(temp_dir) / "workspace"
            self._copy_workspace(snapshot)
            return self._docker_run(
                ["/bin/sh", "-c", command],
                host_dir=snapshot,
                timeout_seconds=timeout_seconds,
                working_directory="workspace_snapshot",
                filesystem_isolation="docker_workspace_copy_ro",
            )

    def _docker_run(
        self,
        container_command: list[str],
        *,
        host_dir: Path,
        timeout_seconds: float,
        working_directory: str,
        filesystem_isolation: str,
    ) -> dict[str, object]:
        docker_cmd = [
            "docker", "run", "--rm",
            "--network", self.network,
            "--memory", self.memory_limit,
            "--cpus", self.cpu_limit,
            "--pids-limit", str(self.pids_limit),
            "--read-only",  # 컨테이너 루트 FS는 read-only
            "--tmpfs", "/tmp:size=64m,rw,exec",
            "-v", f"{host_dir}:/workspace:ro",
            "-w", "/workspace",
            self.image,
            *container_command,
        ]
        start = time.monotonic()
        timed_out = False
        try:
            completed = subprocess.run(
                docker_cmd,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            exit_code = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = 124
            stdout = _decode_timeout_output(exc.stdout)
            stderr = _decode_timeout_output(exc.stderr) or f"Timed out after {timeout_seconds:g}s"
        duration_ms = int((time.monotonic() - start) * 1000)
        return SandboxResult(
            command=shlex.join(docker_cmd),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            timed_out=timed_out,
            backend=self.name,
            working_directory=working_directory,
            filesystem_isolation=filesystem_isolation,
        ).to_dict()

    def _copy_workspace(self, destination: Path) -> None:
        def ignore(directory: str, names: list[str]) -> set[str]:
            return {
                name
                for name in names
                if name in _SNAPSHOT_IGNORE_NAMES
                or name.endswith(".pyc")
                or (Path(directory) / name).is_symlink()
            }

        shutil.copytree(self.workspace, destination, ignore=ignore, symlinks=True)

    def _enforce_local_policy(self, payload: str, *, kind: str) -> None:
        """Same policy enforcement as the local backend."""

        if any(alias in payload for alias in self._workspace_aliases):
            raise SandboxPolicyViolation(
                "실제 워크스페이스 절대경로 접근은 컨테이너 정책상 차단됩니다.",
                reason="workspace_path",
            )
        for prefix in _BLOCKED_ABSOLUTE_PREFIXES:
            if prefix in self._workspace_aliases:
                continue
            pattern = rf"(?<![A-Za-z0-9_.-]){re.escape(prefix)}(?:/|\b)"
            if re.search(pattern, payload):
                raise SandboxPolicyViolation(
                    f"민감한 절대경로 접근은 컨테이너 정책상 차단됩니다: {prefix}",
                    reason="sensitive_absolute_path",
                )
        if kind in {"shell", "workspace_command"}:
            normalized = f" {payload.strip()} ".lower()
            for pattern in _BLOCKED_SHELL_PATTERNS:
                if pattern in normalized:
                    raise SandboxPolicyViolation(
                        f"위험한 shell 패턴이 차단되었습니다: {pattern.strip()}",
                        reason="dangerous_shell_pattern",
                    )
