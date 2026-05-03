# Architecture Decision Log

이 문서는 AdaptiveAgent의 큰 아키텍처 방향, 사용자 결정, 추가 요구사항, 설계상 중요한 판단을 추적하는 회의록입니다.

큰 그림은 `docs/architecture_blueprint.md`, 세부 구현 방법은 `docs/basic_architecture_design.md`, 검증 시나리오는 `docs/adaptive_agent_validation_scenarios.md`, 연구 근거는 `reference.md`와 `docs/research/`에 둡니다. 이 파일은 “무엇을 왜 결정했는가”만 남깁니다.

## 기록 원칙

- 세부 코드 diff나 함수 단위 구현 기록은 적지 않는다.
- 아키텍처 경계, 역할 분리, 저장 정책, HITL 정책처럼 이후 구현에 영향을 주는 결정만 적는다.
- 사용자가 내린 결정과 추가 요구사항은 날짜와 함께 남긴다.
- 결정이 바뀌면 기존 항목을 지우기보다 새 항목으로 변경 이유를 남긴다.

## 현재 큰 그림

AdaptiveAgent는 CLI 중심의 프레임워크 독립형 에이전트입니다. 핵심 흐름은 `AgentState`를 공유 상태로 삼고, `StateMachineRouter`가 노드 전이를 제어하며, 역할별 프롬프트와 툴/스킬 저장 계층을 분리하는 방향입니다.

```text
CLI
  -> AdaptiveAgent
  -> StateMachineRouter
  -> AgentState
  -> Plan / Coder / Critic
  -> ToolRegistry / LocalSandboxBackend
  -> SkillCatalog(manifest.json)
```

## 확정 결정

### 2026-05-02: Agent Core는 상태와 라우터 중심으로 확장한다

- `AdaptiveAgent.run()`에 모든 분기를 계속 쌓지 않는다.
- 공유 상태는 `AgentState`가 담당한다.
- 흐름 제어는 `StateMachineRouter`와 `next_node` 기반으로 분리한다.
- 초기 역할별 구현은 `nodes/` 계약을 따랐으나, 이번 MVP에서 `agents/` 계약으로 승격한다.

### 2026-05-02: 프롬프트는 코드 밖 파일로 관리한다

- 시스템/역할 프롬프트는 `adaptive_agent/prompts/default/*.txt`에 둔다.
- 지시어는 영어로 작성한다.
- 코드에는 동적 값 렌더링과 실행 계약만 둔다.
- 프롬프트 변경이 쉬워야 하므로 하드코딩된 장문 지시문은 피한다.

### 2026-05-02: 역할 프롬프트는 Plan, Coder, Critic 중심으로 둔다

- `Plan Agent`: 사용자 원문과 available tools를 보고 다음 action을 결정한다.
- `Coder Agent`: 승인된 계획을 바탕으로 재사용 가능한 Python tool code를 만든다.
- `Critic Agent`: 실행 결과와 원래 의도를 비교해 성공, 실패, 재시도, 사용자 입력 필요 여부를 판단한다.
- `Skill`은 현재 LLM 프롬프트 노드가 아니라 `SkillCatalog` 기반 저장/metadata 계층이다.

### 2026-05-02: 이번 구현에서 역할별 agent를 분리한다

- `nodes/` 경계를 유지하는 수준이 아니라 Plan, Coder, Executor, Critic, Librarian 역할을 별도 agent 계약으로 분리한다.
- 기존 `adaptive_agent/nodes/`는 현재 코드 기준선이며, 새 구현은 `adaptive_agent/agents/` 경계로 이동하거나 호환 shim을 둔다.
- `StateMachineRouter`는 계속 `AgentState.next_node`를 기준으로 전이하되, node 구현 세부가 아니라 역할별 agent를 호출하도록 정리한다.
- 각 agent는 공유 `AgentState`를 사용하되 입력/출력 계약, 이벤트 기록, 실패 책임 범위를 명확히 가진다.

### 2026-05-02: 생성 툴은 사용자 승인 후에만 manifest에 등록한다

- `tool_create`는 생성 파일과 개별 metadata만 만든다.
- `tool_validate`는 샌드박스 검증 결과만 기록한다.
- `tool_approve`가 사용자 승인 후 `.adaptive_agent/tools/manifest.json`에 등록한다.
- `tool_search`는 승인되어 manifest에 들어간 생성 툴만 검색 후보로 본다.

### 2026-05-02: 특정 작업용 정규식/하드코딩보다 큰 경계를 우선한다

- 임시 intent 분류 정규식을 늘리는 방식은 피한다.
- 새 동작은 가능한 한 `AgentState`, `StateMachineRouter`, `nodes`, `prompts`, `SkillCatalog` 경계 안에 배치한다.
- 구조화된 JSON action 계약을 우선 확장한다.

### 2026-05-02: 다음 MVP는 승인 툴 재사용 루프를 완성한다

- 목표는 `tool_approve`로 manifest에 등록된 생성 툴이 다음 세션에서 실제 실행 가능한 `ToolRegistry` 항목으로 로드되는 것이다.
- 검색 후보와 실행 가능 툴이 분리되어 `Unknown tool`로 끝나는 간극을 먼저 닫는다.
- 새 툴 생성 전에는 내장 툴과 승인된 manifest를 Top-K로 검색하고, 중복 생성을 피한다.
- 저장 정책은 유지한다. 생성 툴은 검증과 사용자 승인 후에만 `manifest.json`에 등록된다.
- HITL은 단순 응답 상태가 아니라 session id로 재개 가능한 제어 흐름으로 확장한다.
- Critic reflection은 retry 시 다음 planning context에 전달한다.

### 2026-05-02: CLI session은 기본 새 session으로 시작한다

- CLI를 종료했다가 다시 실행하면 기본적으로 새 session을 시작한다.
- 사용자가 명시적으로 원할 때만 이전 session을 복구해 이어간다.
- 이번 구현에서는 pending HITL 재개에 필요한 최소 snapshot만 저장한다.
- 전체 history 압축, session 만료, 민감정보 제거, model별 context budget 관리는 장기 과제로 둔다.
- session id 존재 여부, pending 상태 여부, workspace 내부 session 파일 여부를 검증한다.
- 완료/거부/실패로 닫힌 session은 다시 resume하지 않는다.
- session 파일 누적 가능성과 수동 삭제 경로를 사용자에게 안내한다.

### 2026-05-02: 승인된 generated tool도 subprocess에서 실행한다

- 승인된 생성 툴을 메인 프로세스에서 직접 import해 실행하지 않는다.
- 검증·승인된 manifest 항목만 실행 후보로 로드하되, runtime 실행은 subprocess 경계를 유지한다.
- stdout/stderr/exit code 요약을 CLI/JSON 출력에 남겨 성능과 디버깅성을 보완한다.
- manifest와 generated tool 파일이 불일치하거나 파일이 사라진 경우 loader 실패로 분류한다.
- Docker, virtualenv, 제한 유저, 개발 환경별 sandbox profile은 이후 환경이 정해진 뒤 검토한다.

### 2026-05-02: Docker sandbox 제거 — subprocess LocalSandboxBackend만 유지

- `DockerSandboxBackend`, `is_docker_available()`, `create_sandbox()`, `_copy_workspace_to()` 제거.
- `LocalSandboxBackend`(subprocess) 단일 백엔드로 정리.
- `config.py`의 `sandbox_backend`, `docker_image`, `docker_memory` 필드 제거.
- 이유: 운영 환경에서 Docker를 요구하지 않도록 의존성을 최소화. 컨테이너 sandbox는 향후 환경이 확정되면 재검토.

### 2026-05-02: Anthropic·Grok provider 제거

- `config.py`의 `anthropic_model`, `grok_model` 필드 제거, `from_env()` 항목 제거.
- `llms/factory.py`에서 해당 분기 제거.
- 이유: 사용자가 구현하지 않기로 결정. OpenAI·Gemini·Ollama 3가지 provider만 유지.

### 2026-05-02: Multi-agent 병렬 실행 구현

- `{"action":"parallel","actions":[...]}` 정규화 플랜 형식 추가.
- `ExecutorAgent._run_parallel_plan()`이 `ThreadPoolExecutor`로 sub-action들을 동시 실행.
- 결과는 `AgentState.parallel_results`에 수집. 상태 동시 쓰기 경쟁을 피하기 위해 병렬 실행 중 `last_tool_name` 등 단일 상태는 갱신하지 않음.
- `PlanAgent`의 next_node 라우팅에 `"parallel"` action 조건 추가(`agents/plan.py`).

### 2026-05-02: 서비스 정식 실행 스크립트 추가 (scripts/start.sh)

- 환경 점검(Python 버전, .venv, requirements.txt, .env 로드, provider 자동 감지), 단위 테스트, LLM smoke test, 대화형 CLI 루프를 하나의 스크립트로 통합.
- `--check-only`, `--provider`, 단일 task 인수 지원.

### 2026-05-02: AAVS 시나리오 테스트에서 발견한 시스템 버그 3건 수정

AAVS(AdaptiveAgent Validation Scenarios)를 OpenAI·Gemini·Ollama에서 실제 실행하면서 시스템 수준 버그를 발견하고 수정했다. 테스트 조건을 우회하는 방식이 아니라 시스템 자체를 수정하는 방향으로 진행.

**버그 1 — 자가 교정 성공 후 error_log 미초기화**
- 증상: 자가 교정 루프에서 재실행이 성공해도 `state.error_log`에 이전 실패 에러가 남아있어, Critic이 성공한 실행을 `retry`로 분류.
- 원인: `ExecutorAgent._run_self_correction_loop()`의 성공 분기에서 `error_log` 초기화 누락.
- 수정: `if outcome.success:` 직전에 `state.error_log = ""` 추가.

**버그 2 — Critic의 next_node 직접 기입 신뢰**
- 증상: LLM이 `{"verdict":"success","next_node":"approve"}` 같이 verdict와 맞지 않는 next_node를 직접 기입하면 그대로 승인 대기 상태로 빠짐.
- 원인: `agent.py`의 `_normalize_critique()`가 LLM이 반환한 `next_node`를 그대로 신뢰.
- 수정: verdict를 기준으로 next_node를 항상 코드에서 결정하는 `_verdict_to_node` 매핑 도입. LLM의 next_node 값은 무시.

**버그 3 — Critic의 과거 reflection 편향**
- 증상: 첫 실행 실패(stdin 의존) → Critic이 `retry` + reflection 기록. 두 번째 실행 성공(데이터 직접 임베드) → Critic이 여전히 `retry` 반환. `state.reflections`에 첫 실패 반성이 남아 LLM Critic을 편향시킴.
- 원인: `critic.txt` 프롬프트가 prior reflections를 참조할 때 현재 성공 여부보다 과거 실패 정황을 우선하는 경향.
- 수정: `critic.txt`에 규칙 추가 — "latest tool result success=true이고 output이 비어있지 않고 error log가 비어있으면 prior reflections와 무관하게 verdict:success를 반환해야 한다. prior reflections는 역사적 맥락이며 현재 성공한 실행을 override하지 않는다." EXECUTION WORKFLOW에도 latest tool result를 가장 먼저 확인하는 단계 명시.

## 추가 요구사항

### 2026-05-02: 작업 전달 방식

- 작업 시작 시 현재 역할, 다음 역할자, 사용자 확인 필요사항을 제시한다.
- 작업 종료 시 전달 블록을 남긴다.
- 다음 역할자가 무엇을 확인해야 하는지 명확히 남긴다.

### 2026-05-02: 아키텍처 회의록 유지

- 큰 그림 설계, 결정 변경, 사용자 요구사항, 구현 방향 의견을 이 파일에 누적한다.
- 세부 구현보다 이후 설계와 구현에 영향을 주는 판단을 우선 기록한다.

### 2026-05-02: 큰 그림 설계 문서 분리

- `docs/basic_architecture_design.md`는 세부 구현 사항이 많아 외부인이 큰 흐름을 보기 어렵다.
- 외부인과 사용자 본인이 전체 구조를 추적할 수 있도록 `docs/architecture_blueprint.md`를 별도 진입점으로 둔다.
- 큰 그림 문서는 핵심 구성, 데이터 흐름, action 계약, 저장 정책, 현재 구현 상태만 유지한다.

## 열린 질문

### 이번 구현 전 결정 필요

- HITL session snapshot 저장 위치와 최소 저장 필드는 어떻게 둘 것인가? 초기안은 `.adaptive_agent/sessions/` 파일 저장과 민감정보 미저장이다.
- CLI 재개 UX는 명령어 수를 늘리지 않으면서 어떻게 안내할 것인가? 초기안은 pending 응답에 다음 resume 명령을 제시하는 것이다.

### 장기 검토

- `AgentState.next_node`를 `Literal`로 유지할지, 런타임 검증 가능한 `Enum`으로 바꿀 것인가?
- SkillCatalog 검색은 당분간 키워드 Top-K로 두고, embedding vector를 직접 저장할지 별도 index 참조만 둘지는 툴 수가 늘어난 뒤 결정한다.
- session history 압축, session 만료, model별 context budget, prompt별 context 우선순위는 어떻게 관리할 것인가?
- CLI가 서비스/API로 확장될 경우 사용자 구분, 권한, 보관 기간, 동시성 관리는 어떻게 둘 것인가?
- Docker, virtualenv, 제한 유저 등 더 강한 generated tool sandbox는 어떤 개발/배포 환경을 기준으로 선택할 것인가?

## 다음 검토 후보

- `nodes/`에서 `agents/`로 이동할 때 기존 import 호환성을 얼마나 유지할지 결정
- Plan 이후 Coder/Executor/Critic/Librarian agent 실행 루프 연결
- `use_tool`, `create_tool`, `approve_tool`, `final_answer` 계약의 provider별 안정성 확인
- `manifest.json` metadata schema와 migration 정책
- AAVS-004, AAVS-005 기반 저장 승인/재사용 검증

---

## 2026-05-03 회고 — AAVS 검증 사이클 완료

### 달성된 것

**AAVS 16개 시나리오 기준 최종 결과:**

| 모델 | 수정 전 | 수정 후 | 개선 |
|------|:------:|:------:|:---:|
| gpt-5.4-nano | 10/16 | 15/16 | +5 |
| gpt-5.4-mini | 10/16 | 15/16 | +5 |
| qwen3.5:2b | 8/16 | 9/16 | +1 |
| qwen3.5:4b | 8/16 | 9/16 | +1 |
| qwen3.5:9b | (신규) | 9/16 | — |

OpenAI 모델 기준 5개 시나리오 개선: Critic 과잉 retry, `tool_validate` sample 없이 실패, JSON 파서 rfind 오작동, plan JSON 잘림, tool_approve 검증 기준. 이 버그들은 OpenAI에서 발현 빈도가 낮았을 뿐 qwen 스트레스 테스트로 근본 결함을 드러냈다.

**주요 수정 항목:**
- `agent.py` — Critic `success=True` 시 무조건 retry 차단, `_extract_json_object` balanced-bracket 교체, `_repair_truncated_json` dynamic suffix
- `coder.py` — `_unwrap_double_encoded_code`: qwen 이중인코딩 복원 (trailing `)"`, `"}` 누락, regex `"\)(}+)$` 패턴)
- `builtins.py` — `tool_validate` sample 없을 때 구조 검증만, `tool_approve` `validated=True` 기준으로 완화
- `config.py` — `max_router_steps` 8 → 12

### qwen 잔존 실패 원인 정리

AAVS 7개 실패 전부 Coder 단계에서 발생. 플래너는 올바르게 동작함.

| 실패 유형 | 대표 시나리오 | 근본 원인 |
|---------|------------|---------|
| 템플릿 과적합 | 007, 011, 013 | coder.txt "filter → aggregate" 예시를 모든 집계 태스크에 적용. 모델이 클수록 더 충실히 잘못 따름 |
| 사전학습 편향 | 009, 010 | "CSV = 파일 열기", "손상 JSON = 자동 수정" 편향이 프롬프트 지시보다 강함. 크기 무관 |
| MODE B 이중인코딩 | 014, 015 | qwen3.5 계열 전체에서 tool_create 출력이 `{"code":"def run..."}` 구조로 중첩. 9b도 동일 |

AAVS-013 비교가 특히 명확: nano는 `r['product']['name']`, `sum(o['quantity'] for o in r['orders'])` 중첩 순회 1회 성공. qwen3.5:9b는 `filtered = [r for r in records if r['product']['price'] > 10]` 후 평균 계산으로 대치.

### 발견: 모델 크기 증가로 해결되지 않는 이유

- 템플릿 과적합: 더 큰 모델이 coder.txt 예시를 더 정확히 따름 → 잘못된 템플릿도 더 충실히 적용
- 사전학습 편향: 파인튜닝 데이터의 강한 코딩 패턴(file I/O, auto-repair)이 instruction보다 우선
- qwen3.5 계열 구조적 한계: 2b/4b/9b 모두 동일한 이중인코딩 패턴 → 아키텍처 레벨 문제

### 시간이 있다면 — 우선순위 순

**1. 코더 역할에 모델을 선택적으로 분리**

현재 모든 역할(플래너, 코더, 크리틱)이 동일 모델을 사용. 하지만 데이터로 확인된 사실:
- qwen3.5 플래너: 대부분 정상 동작 (`code_execute` / `tool_create` 선택 정확)
- qwen3.5 코더: 핵심 병목

`_code_with_llm`에 별도 LLM 클라이언트를 주입하는 방식으로 구현 가능. `config.py`에 `coder_llm_provider` / `coder_model` 추가 수준.

```
config 예시:
  llm_provider = "ollama"        ← 플래너, 크리틱
  coder_provider = "ollama"
  coder_model = "qwen2.5-coder:7b"  ← 코더만 코딩 특화 모델
```

**2. 코딩 특화 모델 교체 테스트**

qwen3.5는 general instruction-following 모델. 코드 생성 특화 모델 후보:
- `qwen2.5-coder:7b` — 동급 크기, 코딩 파인튜닝
- `deepseek-coder-v2:16b` — 코드 생성 벤치마크 상위권
- `codellama:13b` — Meta 코딩 특화, instruction-following 안정

특히 AAVS-013(중첩 JSON 순회), AAVS-010(inline 임베딩) 같은 케이스가 직접적 검증 대상.

**3. Critic에 출력 합리성 검사 추가**

현재 Critic은 "실행 성공 + JSON 출력 있으면 success". AAVS-007/011/013 모두 틀린 값을 success로 통과시킴. task 원문과 실행 결과를 같이 주고 수치/구조 합리성을 확인하는 critic.txt 규칙 추가 검토.

**4. correction이 동일 실수를 반복하는 루프 방지**

AAVS-010: `open()` 사용 오류가 self-correction 4회 전부 반복. 이전 시도의 실패 패턴을 `state.reflections`에 축적하는 방식은 있지만 correction이 이를 충분히 활용하지 못함. correction 프롬프트에 "이전 시도에서 이미 시도한 방법은 반복하지 않는다" 명시 또는 `forbidden_patterns` 메커니즘 검토.

**5. 모델 패밀리별 프롬프트 분기**

```
prompts/
  default/coder.txt        ← fallback
  openai/coder.txt         ← 현재와 동일, 상세 예시
  ollama-general/coder.txt ← qwen류: 예시 단순화, 제약 규칙 강조
  ollama-coder/coder.txt   ← deepseek-coder류: 코드 예시 풍부
```

`PromptLoader`에 `{provider}/{model_family}` fallback 체인 추가. 현재 단일 프롬프트가 모든 모델에 적용되는 것이 qwen 성능의 주요 제약 중 하나.

### 열린 질문 추가

- `qwen2.5-coder:7b`를 코더 역할에 한정해 사용하면 qwen3.5 플래너 조합 대비 AAVS 점수가 실질적으로 개선되는가?
- 역할별 모델 분리 시 context(state.reflections, last_tool_result 등)를 어떻게 효율적으로 전달할 것인가?
- Critic의 수치 합리성 검사를 LLM에 맡기면 latency/비용이 어느 수준인가? rule-based fallback이 더 나은가?
