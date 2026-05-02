# Changelog

All notable changes to AdaptiveAgent are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project loosely uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Until the first stable release the API is alpha; minor versions may break.

## [Unreleased]

PRs in flight that target this changelog entry once merged:

- **Session cleanup** (#13/PR #22) — TTL/cap policy + `agent_initialized` cleanup summary
- **Critic reflection contract test** (#14/PR #23)
- **agent.py decomposition Phase 1** (#15/PR #25) — extract execute/self-correction loop into `agents/executor.py`
- **SkillCatalog dedup Phase 1** (#16/PR #24) — preserve usage stats + `manifest_entry_merged` event
- **artifact_store + web_fetch builtins** (#19/PR #26) — domain-allowlisted HTTP fetch and sha256-keyed artifact store
- **Packaging** (PR #27) — `pyproject.toml` + `adaptive-agent` console script

Tracked but not yet implemented (open issues):

- #17 Embedding-based Top-K skill search (sentence-transformers / OpenAI option)
- #18 Container sandbox option (Docker opt-in)
- #20 Multi-perspective parallelization CLI
- Cost / token usage tracking
- Streaming responses
- Skills classification policy (planning / functional / atomic)

## [0.1.0] — 2026-05-02

Initial alpha. Brings the first feature-complete agent core that can plan, execute, validate, approve, and reuse generated tools end-to-end through both the natural-language router and the explicit `--tool` CLI.

### Added — agent core

- `AgentState` shared state + `StateMachineRouter` (retrieve → plan → {code | execute} → critique → done/approve/error transitions, `max_steps` guard, `unknown_next_node` fallback).
- Role-specific agents (`adaptive_agent/agents/`): `PlanAgent`, `CoderAgent`, `ExecutorAgent`, `CriticAgent`, `LibrarianAgent`.
- File-backed prompt loader (`prompts/default/{plan,coder,critic,correction}.txt`) with `{slot}` interpolation and missing-slot detection.
- Bounded self-correction loop with `max_self_corrections`, exposed via `failure_classified` / `self_correction_started` / `tool_reexecuted` events.
- HITL pending-session lifecycle: `agent.resume()` + `SessionStore.save_pending/load_pending/close` + CLI `--resume/--approve/--reject/--input`.
- Critic verdict → next_node routing (`success` / `retry` / `approval_required` / `failed` / explicit `next_node` override).
- `CoderAgent` validates required `tool_create` arguments (`name`/`description`/`code`) before execution and emits `coder_arguments_invalid`.
- `LibrarianAgent` retrieves skill candidates and (when wired to a `SkillCatalog`) audits manifest integrity, emitting `catalog_audit_stale_entries`.
- `_run_normalized_plan` decomposed into `_execute_normalized_tool` + `_run_self_correction_loop` + `_ToolAttemptOutcome` for clearer responsibility boundaries.

### Added — built-in tools (20)

`echo`, `analyze_requirements`, `list_tools`, `list_files`, `code_execute`, `shell_run`, `file_read`, `file_write`, `file_list`, `file_patch`, `ask_human`, `propose_actions`, `test_run`, `tool_create`, `tool_search`, `tool_validate`, `tool_approve`, `memory_read`, `memory_write`, `suggest_builtin_tools`.

### Added — sandbox & policy

- `LocalSandboxBackend` (subprocess + temp dir + minimal env) for code/shell/test execution.
- Policy enforcement with machine-readable `block_reason` enum: `workspace_path` / `sensitive_absolute_path` / `dangerous_shell_pattern`.
- macOS `/private/var` resolve discrepancy fix: workspace alias set covers both raw and resolved forms so policy checks fire correctly.

### Added — skill catalog

- `SkillCatalog` with manifest schema_version, weighted Top-K keyword search (name 4× / description 2× / tags 1.5× / category·parameters 1×), dedupe.
- Generated tool loader rejects entries with missing file, missing hash, or hash mismatch (`generated_load_results` exposes per-entry status).
- `record_usage(name, success)` increments `usage_count` / `failure_count` per execution; called once at the single `agent.run_tool` entry point so direct `--tool` calls and router executions both update stats.
- `find_stale_entries()` returns `missing_path` / `missing_file` / `missing_hash` / `hash_mismatch` violations.
- `generated_tool_usage_recorded` event surfaces post-call counters.

### Added — CLI

- `--list-tools` (text and `--json`).
- `--tool <name> --arg key=value` for explicit tool execution. Values that look like JSON (start with `{`/`[`/digit/`-` or are exactly `true`/`false`/`null`) are auto-decoded so nested dict/list arguments work end-to-end.
- `--resume <session_id>` + `--approve` / `--reject` / `--input <text>` for HITL session resume.
- `--json` for structured output.
- Resume errors include `session_id` so multi-session operators can identify the failure.

### Added — testing & CI

- 124 unit tests covering router transitions, prompt-placeholder contract, session store path validation, generated-tool lifecycle, librarian audit + usage delegation, input variation matrices, and JSON-arg coercion.
- GitHub Actions workflows: `pr-validation.yml` (unit tests + smoke CLI) and `manual-llm-check.yml` (opt-in real-LLM validation against ollama / openai / gemini).
- AAVS validation harness (`scripts/aavs_validate.py`).

### Added — docs

- `docs/architecture_blueprint.md` (state table kept in sync with code per PR).
- `docs/basic_architecture_design.md`, `docs/architecture_decision_log.md`, `docs/requirements_breakdown.md`, `docs/adaptive_agent_validation_scenarios.md`, `docs/research/README.md`.
- `.cursor/rules/` and `CLAUDE.md` describe the persona-based workflow that produced this release.

### Known limitations

- LLM providers: Ollama / OpenAI / Gemini supported. Anthropic and MCP are explicitly out of scope.
- `agent.py` is still ~870 lines (only the bounded execution loop has moved out so far). Full module split tracked in #15 Phase 2-4.
- No semantic skill dedup yet (#16 Phase 2).
- No embedding-based search, container sandbox, multi-agent parallelization, cost tracking, or streaming yet (issues #17 / #18 / #20 + untracked work).
