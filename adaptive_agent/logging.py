"""Structured CLI logger for AdaptiveAgent execution."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, TextIO

_RESET   = "\033[0m"
_BOLD    = "\033[1m"
_DIM     = "\033[2m"
_GREEN   = "\033[32m"
_YELLOW  = "\033[33m"
_CYAN    = "\033[36m"
_RED     = "\033[31m"
_BLUE    = "\033[34m"
_MAGENTA = "\033[35m"

# (display_label, agent_class_name, ansi_color)
_NODE: dict[str, tuple[str, str, str]] = {
    "retrieve": ("retrieve", "LibrarianAgent", _CYAN),
    "plan":     ("plan    ", "PlanAgent     ", _BLUE),
    "code":     ("code    ", "CoderAgent    ", _MAGENTA),
    "execute":  ("execute ", "ExecutorAgent ", _YELLOW),
    "critique": ("critique", "CriticAgent   ", _CYAN),
    "synthesize": ("synthesize", "SynthesizerAgent", _GREEN),
    "done":       ("done      ", "                ", _GREEN),
    "approve":    ("approve   ", "                ", _YELLOW),
    "error":      ("error     ", "                ", _RED),
}


def _use_color(stream: TextIO) -> bool:
    return (
        hasattr(stream, "isatty")
        and stream.isatty()
        and not os.getenv("NO_COLOR")
        and os.getenv("TERM") != "dumb"
    )


class AgentLogger:
    """Prints agent node progress to stderr and appends JSONL events to log_file."""

    def __init__(
        self,
        *,
        quiet: bool = False,
        log_file: Path | None = None,
        stream: TextIO | None = None,
    ) -> None:
        self.quiet = quiet
        self._out = stream if stream is not None else sys.stderr
        self._color = _use_color(self._out)
        self._fh = open(log_file, "a", encoding="utf-8") if log_file else None  # noqa: SIM115

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None

    # ── hooks called by StateMachineRouter ───────────────────────────────────

    def on_task_start(self, task: str) -> None:
        self._jsonl({"event": "task_start", "task": task})
        if self.quiet:
            return
        sep = "─" * 52
        self._p(f"\n{sep}")
        self._p(f"  작업: {task!r}")
        self._p(f"{sep}\n")

    def on_node_enter(self, node: str, state: Any) -> None:
        self._jsonl({"event": "node_enter", "node": node})
        if self.quiet:
            return
        meta = _NODE.get(node)
        label, agent, color = meta if meta else (node.ljust(8), "", "")
        bracket   = self._c(f"[{label}]", color + _BOLD)
        agent_fmt = self._c(agent, _DIM)
        detail    = self._enter_detail(node, state)
        self._p(f"{bracket}  {agent_fmt}  {detail}")

    def on_node_exit(self, node: str, state: Any) -> None:
        self._jsonl({
            "event": "node_exit",
            "node": node,
            "next": getattr(state, "next_node", ""),
        })
        if self.quiet:
            return
        detail = self._exit_detail(node, state)
        if detail:
            # 멀티라인 detail: 첫 줄은 └─ prefix, 이후 줄은 동일 indent로 출력
            lines = detail.split("\n")
            self._p(f"           └─ {lines[0]}")
            for line in lines[1:]:
                self._p(f"             {line}")

    def on_self_correction(self, attempt: int, error: str, tool_name: str) -> None:
        self._jsonl({"event": "self_correction", "attempt": attempt, "tool_name": tool_name})
        if self.quiet:
            return
        err_preview = (error or "")[:60].replace("\n", " ")
        label = self._c(f"[수정 {attempt}]", _YELLOW + _BOLD)
        self._p(f"{label}  오류 원인 분석 후 재시도 중...  {err_preview}")

    def on_final(self, output: Any, action: str) -> None:
        self._jsonl({"event": "final", "action": action})
        if self.quiet:
            return
        is_err = "error" in action
        status = self._c("오류" if is_err else "완료", _RED if is_err else _GREEN)
        sep = "─" * 52
        self._p(f"\n{sep}")
        self._p(f"  {status}  ({action})")
        self._p(sep + "\n")

    # ── internal ──────────────────────────────────────────────────────────────

    def _enter_detail(self, node: str, state: Any) -> str:
        if node == "retrieve":
            return "기존 스킬 검색 중..."
        if node == "plan":
            return "계획 수립 중..."
        if node == "code":
            plan = getattr(state, "current_plan", {}) or {}
            tool = plan.get("arguments", {}).get("name") or plan.get("tool_name", "")
            return f"코드 생성 중...  ({tool})" if tool else "코드 생성 중..."
        if node == "execute":
            plan = getattr(state, "current_plan", {}) or {}
            tool = plan.get("tool_name", "") or getattr(state, "last_tool_name", None) or ""
            args = plan.get("arguments") or getattr(state, "last_tool_arguments", {}) or {}
            preview = ""
            if args:
                try:
                    preview = json.dumps(args, ensure_ascii=False)[:60]
                except Exception:
                    pass
            return f"{tool} 실행 중...  {preview}".strip() if tool else "실행 중..."
        if node == "critique":
            n = getattr(state, "failure_count", 0) + 1
            return f"결과 평가 중...  (시도 {n})"
        if node == "synthesize":
            return "작업 완료 처리 중..."
        if node == "done":
            return self._c("완료 " + "─" * 35, _GREEN)
        if node == "approve":
            return "사용자 승인 대기 중..."
        if node == "error":
            err = getattr(state, "error_log", "") or "오류 발생"
            return self._c(err[:80], _RED)
        return ""

    def _exit_detail(self, node: str, state: Any) -> str:
        if node == "retrieve":
            skills = getattr(state, "retrieved_skills", None) or []
            n = len(skills)
            if n == 0:
                return "검색된 스킬 없음"
            # 스킬 이름 추출: dict이면 "name" 키, 문자열이면 그대로
            names: list[str] = []
            for s in skills:
                if isinstance(s, dict):
                    names.append(s.get("name") or s.get("skill_name") or str(s))
                else:
                    names.append(str(s))
            if n <= 3:
                return f"검색된 스킬 {n}개: {', '.join(names)}"
            shown = ", ".join(names[:3])
            return f"검색된 스킬 {n}개: {shown} 외 {n - 3}개"

        if node == "plan":
            plan   = getattr(state, "current_plan", {}) or {}
            action = plan.get("action", "")
            tool   = plan.get("tool_name") or plan.get("arguments", {}).get("name", "")
            args   = plan.get("arguments") or {}
            parts  = [f"action={action}"]
            if tool:
                parts.append(f"tool={tool}")
            summary_line = "  ".join(parts)

            reasoning = (plan.get("reasoning") or "").strip()
            # tool별 핵심 인자 한 줄 요약
            arg_hint = ""
            if tool == "code_execute":
                task_desc = str(args.get("task") or "").strip()
                if task_desc:
                    arg_hint = f"  ↳ {task_desc[:100]}"
            elif tool == "ask_human":
                q = str(args.get("questions") or args.get("question") or "").strip()
                if q:
                    arg_hint = f"  ↳ {q[:100]}"
            elif action == "respond":
                resp = str(plan.get("response") or "").strip()
                if resp:
                    preview = resp[:60] + ("…" if len(resp) > 60 else "")
                    arg_hint = f"  ↳ {preview}"
            elif tool == "tool_create":
                name = str(args.get("name") or "").strip()
                desc = str(args.get("description") or "").strip()
                if name:
                    arg_hint = f"  ↳ {name}: {desc[:80]}" if desc else f"  ↳ {name}"

            lines = [summary_line]
            if reasoning:
                lines.append(f"💭 {reasoning[:120].replace(chr(10), ' ')}")
            if arg_hint:
                lines.append(arg_hint)
            return "\n".join(lines) if len(lines) > 1 else summary_line

        if node == "code":
            code = getattr(state, "generated_code", None) or ""
            code = code.strip()
            if not code:
                return ""
            lines = code.splitlines()
            first = lines[0][:80] if lines else ""
            return f"{first} [{len(lines)}줄]"

        if node == "execute":
            result = getattr(state, "last_tool_result", None)
            if not isinstance(result, dict):
                return ""
            # output.execution 하위 구조에서 stdout/exit_code/timed_out 추출
            output = result.get("output") or {}
            execution = output.get("execution") or {} if isinstance(output, dict) else {}
            stdout_raw = execution.get("stdout", "") if isinstance(execution, dict) else ""
            exit_code = execution.get("exit_code") if isinstance(execution, dict) else result.get("exit_code")
            timed_out = execution.get("timed_out") if isinstance(execution, dict) else result.get("timed_out")
            # timed_out 우선 표시
            if timed_out:
                return self._c("타임아웃", _YELLOW)
            stdout_str = str(stdout_raw) if stdout_raw is not None else ""
            stdout_preview = stdout_str[:50].replace("\n", " ")
            parts = []
            if exit_code is not None:
                icon = self._c("✓", _GREEN) if exit_code == 0 else self._c("✗", _RED)
                parts.append(f"{icon} exit_code={exit_code}")
            if stdout_preview:
                parts.append(f"stdout: {stdout_preview}")
            return "  ".join(parts) if parts else ""

        if node == "critique":
            nxt = getattr(state, "next_node", "")
            if nxt == "done":
                base = self._c("✓ 통과", _GREEN)
            elif nxt == "error":
                base = self._c("✗ 실패", _RED)
            else:
                base = f"재시도 → {nxt}"
            # execution_critiqued 이벤트에서 reason 추출
            reason = ""
            events = getattr(state, "events", []) or []
            for evt in reversed(events):
                if hasattr(evt, "name") and evt.name == "execution_critiqued":
                    reason = str(evt.details.get("reason") or "").strip()
                    break
            if reason:
                snippet = reason[:80].replace("\n", " ")
                return f"{base} — {snippet}"
            return base

        if node == "synthesize":
            summary = getattr(state, "summary", "") or ""
            text = summary[:100].replace("\n", " ") if summary else ""
            hint = ""
            for evt in reversed(getattr(state, "events", []) or []):
                if hasattr(evt, "name") and evt.name == "synthesis_created":
                    if evt.details.get("needs_code_save"):
                        hint = "💾 저장 여부를 묻습니다"
                    break
            if text and hint:
                return f"{text}\n{hint}"
            return text or hint

        return ""

    def _c(self, text: str, code: str) -> str:
        return f"{code}{text}{_RESET}" if (self._color and code) else text

    def _p(self, text: str) -> None:
        print(text, file=self._out, flush=True)

    def _jsonl(self, record: dict) -> None:
        if self._fh:
            self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._fh.flush()
