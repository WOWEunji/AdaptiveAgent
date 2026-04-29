"""Process sandbox backend abstractions for built-in tools."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

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
    """로컬 정책상 허용하지 않는 실행 요청입니다."""


@dataclass(frozen=True)
class SandboxResult:
    """별도 프로세스 실행 결과를 JSON 직렬화 가능한 dict로 변환합니다."""

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
    """표준 라이브러리만 사용하는 로컬 프로세스 샌드박스 백엔드입니다."""

    name = "local_process"

    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()

    def run_python_code(self, code: str, *, timeout_seconds: float) -> dict[str, object]:
        """Python 코드를 임시 디렉터리의 별도 인터프리터에서 실행합니다."""

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
        """셸 코드를 임시 디렉터리의 별도 프로세스에서 실행합니다."""

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
        """워크스페이스 복사본에서 프로젝트 명령을 실행합니다."""

        self._enforce_local_policy(command, kind="workspace_command", allow_workspace_reference=True)
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
        allow_workspace_reference: bool = False,
    ) -> None:
        """컨테이너 없는 로컬 실행에서 실제 환경을 겨냥한 명령을 사전 차단합니다."""

        if not allow_workspace_reference and str(self.workspace) in payload:
            raise SandboxPolicyViolation("실제 워크스페이스 절대경로 접근은 로컬 정책상 차단됩니다.")

        for prefix in _BLOCKED_ABSOLUTE_PREFIXES:
            if prefix == str(self.workspace):
                continue
            if f'"{prefix}/' in payload or f"'{prefix}/" in payload:
                raise SandboxPolicyViolation(f"민감한 절대경로 접근은 로컬 정책상 차단됩니다: {prefix}")

        if kind in {"shell", "workspace_command"}:
            normalized = f" {payload.strip()} ".lower()
            for pattern in _BLOCKED_SHELL_PATTERNS:
                if pattern in normalized:
                    raise SandboxPolicyViolation(f"위험한 shell 패턴이 차단되었습니다: {pattern.strip()}")


def _decode_timeout_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
