# AdaptiveAgent 기초 아키텍처 설계

큰 그림은 `docs/architecture_blueprint.md`에서 먼저 본다. 이 문서는 구현자가 참고하는 세부 설계, 데이터 구조, 단계별 구현 기준을 다룬다.

## 0. `.cursor` 요구사항 반영 메모

이 설계는 작업 시작 전 확인한 `.cursor/rules/` 요구사항을 기준으로 작성한다.

- 핵심 로직은 LangChain, CrewAI, Claude SDK 같은 에이전트 프레임워크에 의존하지 않는다.
- CLI, Human-in-the-loop, 동적 툴 생성/실행, 자가 수정, 툴 라이브러리 관리를 프로젝트의 중심 흐름으로 둔다.
- 구현 전 설계·검증 기준을 먼저 정리하고, 이후 코드는 `reference.md`, `docs/requirements_breakdown.md`, `docs/adaptive_agent_validation_scenarios.md`와 충돌하지 않게 확장한다.
- LLM provider별 API 차이는 `adaptive_agent/llms/` 어댑터에 격리하고, 에이전트 코어는 내부 표준 인터페이스만 호출한다.
- API 키와 비밀값은 코드나 문서에 넣지 않는다.

## 1. 설계 이유와 목적

큰 아키텍처 결정, 사용자 결정, 추가 요구사항, 회의록 성격의 기록은 `docs/architecture_decision_log.md`에 누적한다. 이 문서는 현재 구조와 세부 설계 기준을 설명한다.

### 설계 이유

고수준 에이전트 프레임워크는 빠른 프로토타이핑에는 유용하지만, 프로덕션에서 다음 위험을 숨기기 쉽다.

- LLM이 어떤 근거로 툴을 선택했는지 추적하기 어렵다.
- 툴 실행 실패, 재시도, 중단 조건이 프레임워크 내부에 감춰질 수 있다.
- 동적 생성 코드가 비멱등 작업을 반복하거나, 세션 재시작 후 메모리가 초기화되어 같은 실패를 반복할 수 있다.
- 사용자 승인 없이 생성 툴이 저장되거나 재사용되는 흐름을 통제하기 어렵다.

### 설계 목적

AdaptiveAgent는 에이전트의 핵심 제어 흐름을 프로젝트 내부 코드로 명시한다.

1. 사용자 원문 입력을 보존한다.
2. LLM 계획 결과를 구조화된 이벤트로 남긴다.
3. 기존 툴 재사용, 새 툴 생성, 사용자 추가 입력 요청을 분리한다.
4. 생성 툴은 격리 실행, 실패 관찰, 제한된 자가 수정, 사용자 저장 승인 절차를 거친다.
5. 승인된 툴만 영구 레지스트리에 저장하고 이후 세션에서 검색·재사용한다.

## 2. 현재 코드 기준선

현재 저장소는 다음 경계를 이미 갖고 있다.

| 영역 | 현재 파일 | 현재 역할 | 다음 설계상 확장 방향 |
| --- | --- | --- | --- |
| CLI | `adaptive_agent/cli.py` | 단일 자연어 task, 명시 툴 실행, JSON 출력 | HITL 입력 재요청, 저장 승인 플래그, 실행 이벤트 출력 |
| Agent core | `adaptive_agent/agent.py` | 공개 실행 API와 LLM 계획/정규화 호환 계층 | 세부 노드 로직을 `StateMachineRouter`와 역할별 node로 더 이동 |
| State router | `adaptive_agent/router.py` | `PlanNode` 호출과 `AgentState.next_node` 기반 실행 전이 경계 | `retrieve -> plan -> code -> execute -> critique -> approve -> store` 루프 확장 |
| Node contract | `adaptive_agent/nodes/base.py`, `adaptive_agent/nodes/plan.py`, `adaptive_agent/nodes/coder.py`, `adaptive_agent/nodes/critic.py` | Plan/Coder/Critic Agent 공통 인터페이스와 역할별 prompt 연결 | Coder/Critic node 실행 로직 및 출력 검증 |
| Prompt templates | `adaptive_agent/prompts/default/*.txt` | Plan/Coder/Critic/correction 지시문을 파일 기반 영어 prompt로 관리 | 역할별 prompt set 추가 |
| Config | `adaptive_agent/config.py` | env/.env 기반 provider와 작업 디렉터리 설정 | 샌드박스 timeout, env allowlist, 레지스트리 경로 정책 |
| LLM adapters | `adaptive_agent/llms/` | provider별 최소 클라이언트 | provider 차이 격리, 구조화 응답 검증, 오류 메시지 표준화 |
| Tool model | `adaptive_agent/tools/models.py` | `Tool`, `ToolExecutionResult` | `ToolSchema`, input/output schema, provenance, validation status |
| Tool registry | `adaptive_agent/tools/registry.py` | 내장 툴 등록과 조회 | 영구 registry 로드, Top-K 검색, 중복 검사 |
| Tool executor | `adaptive_agent/tools/executor.py` | 등록 툴 실행 | subprocess 격리 실행, timeout, 관찰 로그 |
| Skill catalog | `adaptive_agent/skills/catalog.py` | `.adaptive_agent/tools/manifest.json` 기반 생성 툴 인덱스 | reflection, embedding 참조, 품질 지표 확장 |

## 3. 핵심 설계 원칙

1. **프레임워크 독립성**
   - 에이전트 오케스트레이션, 메모리, 툴 선택, 자가 수정 정책은 외부 에이전트 프레임워크에 맡기지 않는다.
   - provider SDK가 필요한 경우에도 `LLMClient` 어댑터 뒤에 격리하고, agent core에는 전파하지 않는다.

2. **원문 보존과 관찰 가능성**
   - 자연어 task는 trim, 번역, casing 변경 없이 보존한다.
   - 실행 중 `task_received`, `task_analyzed`, `tool_executed`, `tool_result_observed`, `final_response_created` 같은 이벤트를 남긴다.

3. **생성보다 재사용 우선**
   - 새 툴 생성 전에 내장 툴과 승인된 레지스트리를 검색한다.
   - 유사 기능이 있으면 재사용하거나 병합 후보로 표시하고 중복 저장을 피한다.

4. **실패는 숨기지 않고 분류**
   - 실패 원인은 사용자 입력 부족, LLM 계획 오류, 생성 코드 오류, 실행 환경 오류, 외부 provider 오류, 저장 정책 오류 중 하나로 분류한다.
   - self-correction은 설정된 최대 횟수까지만 수행한다.

5. **HITL은 제어 흐름의 일부**
   - 모호한 요청, 권한 부족, 저장 승인, 반복 실패 중단은 사용자에게 명시적으로 묻는다.
   - 사용자 동의 없이 생성 툴을 영구 저장하지 않는다.

## 4. 목표 아키텍처

```text
CLI
  |
  v
AdaptiveAgent
  |
  +-- StateMachineRouter
  |     |
  |     +-- AgentState / EventLog / next_node
  |     +-- Node contracts
  |           +-- Plan Agent
  |           +-- Coder Agent
  |           +-- Critic Agent
  |           +-- SkillCatalog storage boundary
  |
  +-- PromptLoader
  |     +-- prompts/default/plan.txt
  |     +-- prompts/default/coder.txt
  |     +-- prompts/default/critic.txt
  |     +-- prompts/default/correction.txt
  |
  +-- Tool layer
        +-- ToolRegistry / ToolExecutor
        +-- LocalSandboxBackend
        +-- SkillCatalog(manifest.json)
        +-- Built-in and generated tools
```

## 5. 데이터 구조 설계

### 5.1 Message

LLM 또는 내부 이벤트와 연결되는 단일 대화 턴이다.

| 필드 | 설명 |
| --- | --- |
| `role` | `system`, `user`, `assistant`, `tool` |
| `content` | 원문 텍스트 또는 직렬화된 관찰 결과 |
| `tool_call_id` | 툴 호출과 응답을 연결하는 선택 필드 |

### 5.2 ToolSchema

LLM이 툴을 정확히 선택하고, 실행기가 입력을 검증하기 위한 명세다.

| 필드 | 설명 |
| --- | --- |
| `name` | 고유 툴 이름 |
| `description` | 자연어 설명 |
| `parameters` | JSON Schema 형태의 입력 스키마 |
| `returns` | 출력 구조 설명 |
| `safety_level` | `low`, `medium`, `high` 등 안전 등급 |
| `source` | `builtin`, `generated`, `approved_registry` |
| `validation_status` | `unverified`, `passed`, `failed`, `deprecated` |

### 5.3 AgentState

한 실행 세션의 상태를 명시적으로 보관한다.

| 필드 | 설명 |
| --- | --- |
| `history` | Message 목록 |
| `events` | 관찰 가능한 실행 이벤트 목록 |
| `user_task` | 보존된 사용자 원문 task |
| `step_count` | ReAct 루프 반복 횟수 |
| `available_tools` | 현재 컨텍스트에 주입된 툴 schema 목록 |
| `candidate_tools` | 저장 후보인 생성 툴 목록 |
| `retrieved_skills` | 검색된 Top-K 스킬/툴 후보 |
| `current_plan` | LLM이 반환한 현재 계획 |
| `generated_code` | 생성 또는 보정된 임시 코드 |
| `last_tool_name` / `last_tool_arguments` | 마지막 실행 요청의 툴 이름과 인자 |
| `last_tool_result` | 마지막 툴/샌드박스 실행 결과 |
| `error_log` | 실패 분석과 self-correction에 넘길 오류 로그 |
| `reflections` | Critic/Skill Agent가 남기는 교훈과 실패 원인 |
| `next_node` | 라우터가 다음에 호출할 노드 상태 |
| `approval` | HITL 승인/거부 대기 상태 |
| `failure_count` | self-correction 및 실패 중단 판단용 카운터 |
| `summary` | 오래된 history를 압축한 요약 메모리 |

## 6. ReAct 제어 흐름

초기 구현은 동기식 while 루프로 충분하다.

1. `task_received`
   - CLI가 원문 task를 Agent에 전달한다.
2. `task_analyzed`
   - Planner가 LLM에게 원문 task, 현재 툴 schema, 응답 JSON 계약을 전달한다.
3. 분기
   - `clarification_requested`: 사용자 입력이 부족하면 CLI가 일시 정지하고 추가 입력을 받는다.
   - `use_tool`: 기존 툴 실행 계약인 `tool`로 정규화한다.
   - `create_tool`: `tool_create` 실행 계획으로 정규화한다.
   - `approve_tool`: 검증된 툴을 `tool_approve` 실행 계획으로 정규화한다.
   - `final_answer`: 기존 응답 계약인 `respond`로 정규화한다.
4. `tool_executed` / `tool_result_observed`
   - 툴 실행 결과를 Observation으로 AgentState에 추가한다.
5. 실패 시
   - 실패 원인을 분류하고 self-correction 가능 여부를 판단한다.
   - 설정된 최대 횟수를 넘으면 HITL로 전환한다.
6. 성공 시
   - 생성 툴이라면 저장 승인 대기열로 보낸다.
   - 승인된 경우에만 registry에 기록한다.

## 7. 동적 툴 파이프라인

### 7.1 생성 전 검색

- `.adaptive_agent/tools/manifest.json`에서 description, name, parameter 키워드를 대상으로 Top-K 후보를 찾는다.
- 초기 검색은 의존성이 적은 키워드 점수로 시작한다.
- 검색 결과가 충분하면 새 툴을 만들지 않고 기존 툴을 실행한다.

### 7.2 코드 생성과 저장 위치

- 생성 코드는 먼저 임시 작업 디렉터리에 둔다.
- 파일명은 툴 이름을 slug화하고 충돌 시 suffix를 붙인다.
- 영구 저장 전에는 registry에 등록하지 않는다.

### 7.3 메타데이터 추출

- `ast`로 함수 이름, 인자, docstring, return annotation을 읽는다.
- `inspect`는 로딩 후 함수 객체에서 signature 검증에 사용한다.
- docstring이 없거나 타입 힌트가 불충분하면 저장 승인 전 보완 대상으로 표시한다.

### 7.4 격리 실행

- 생성 툴은 메인 프로세스에서 직접 import해 실행하지 않는다.
- subprocess 하네스를 통해 timeout, cwd, env allowlist를 적용한다.
- 기본 정책은 workspace 밖 쓰기 금지, 민감 env 미전달, JSON 직렬화 가능한 입출력이다.

### 7.5 Self-correction

- 실행 실패 시 traceback을 Observation으로 저장한다.
- LLM에는 원본 task, 생성 코드 요약, 오류 로그, 수정 제한 횟수를 함께 제공한다.
- 최대 횟수 초과 또는 권한/입력 부족 실패는 사용자 개입 요청으로 종료한다.

### 7.6 승인과 영구 등록

- 문제 해결 후 CLI가 "이 툴을 저장할까요? (y/n)" 흐름을 표시한다.
- `y`: `tool_approve`가 검증된 metadata를 `.adaptive_agent/tools/manifest.json`에 등록한다.
- `n`: 임시 툴은 재사용 대상에서 제외하고 거부 이벤트를 남긴다.

## 8. 영구 레지스트리 설계

초기 파일은 `.adaptive_agent/tools/manifest.json`으로 통일한다. 개별 `{tool_name}.json` 메타데이터는 생성·검증 중간 상태로 남길 수 있지만, 검색과 장기 저장의 단일 인덱스는 사용자 승인 후 등록되는 `manifest.json`이다.

권장 최소 필드:

```json
{
  "tools": [
    {
      "name": "calculate_number_stats",
      "file_path": ".adaptive_agent/tools/calculate_number_stats.py",
      "description": "숫자 목록의 평균과 중앙값을 계산한다.",
      "parameters": {},
      "returns": {},
      "safety_level": "low",
      "created_at": "2026-04-28T00:00:00Z",
      "validation_status": "passed",
      "usage_count": 0,
      "failure_count": 0,
      "reflections": []
    }
  ]
}
```

## 9. 메모리 압축 설계

초기 AgentState는 전체 history를 보관하되, 임계치를 넘으면 다음 순서로 압축한다.

1. 최근 N개 메시지와 실패/승인 이벤트는 그대로 유지한다.
2. 오래된 Thought/Observation은 요약 문자열로 합친다.
3. 요약에는 사용자 결정, 생성된 툴 이름, 실패 원인, 저장 승인 여부를 반드시 남긴다.
4. 압축 전후 이벤트 수와 요약 생성 시점을 기록한다.

## 10. 단계별 구현 Todo

### Phase 1: 상태와 관찰 가능성

- [x] `Message`, `ToolSchema`, `AgentState`, `AgentEvent` dataclass 추가
- [x] Agent 실행 이벤트 기록 구조 추가
- [x] Blueprint 흐름용 `AgentState` 필드(`current_plan`, `generated_code`, `last_tool_result`, `reflections`, `next_node`) 추가
- [x] `StateMachineRouter` 경계 추가
- [x] `PlanNode`를 라우터의 실제 계획 노드로 연결
- [x] LLM 계획 JSON 계약을 `use_tool`, `create_tool`, `approve_tool`, `clarification_requested`, `final_answer`로 확장
- [ ] `LLMClient` 메서드 명칭을 agent core와 일치하도록 정리

### Phase 2: 툴 schema와 레지스트리

- [ ] `Tool` 모델에 schema, source, validation_status 필드 추가
- [ ] 내장 툴을 ToolSchema로 직렬화해 LLM prompt에 주입
- [x] 영구 registry 파일 포맷을 `.adaptive_agent/tools/manifest.json`으로 확정
- [x] `SkillCatalog`로 사용자 승인된 생성 툴 metadata를 manifest에 upsert
- [ ] Top-K 키워드 검색과 중복 후보 표시 구현

### Phase 3: 동적 툴 생성과 샌드박스

- [ ] 생성 툴 임시 디렉터리 정책 추가
- [ ] `ast` 기반 메타데이터 추출기 구현
- [ ] subprocess 실행 하네스 구현
- [ ] timeout, cwd, env allowlist, JSON 입출력 계약 추가

### Phase 4: Self-correction과 HITL

- [ ] 실패 원인 분류 모델 추가
- [ ] 제한 횟수 기반 self-correction 루프 구현
- [ ] CLI 추가 입력 요청 흐름 구현
- [ ] 생성 툴 저장 승인/거부 workflow 구현

### Phase 5: 검증과 릴리즈 게이트

- [ ] AAVS-001, AAVS-003 우선 통과
- [ ] AAVS-002에서 실제 오류 관찰과 재실행 기록 확인
- [ ] AAVS-004의 저장 `y/n` 분기 확인
- [ ] AAVS-005로 중복 생성 방지 확인
- [ ] AAVS-006으로 권한 부족 요청의 HITL 전환 확인

## 11. 수용 기준

- [ ] 설계가 `reference.md`의 SkillX, EvolveTool-Bench, ToolMaker, ToolLibGen, MCP 참고 방향과 충돌하지 않는다.
- [ ] agent core가 외부 에이전트 프레임워크에 의존하지 않는다.
- [ ] 자연어 원문 보존 원칙을 유지한다.
- [ ] 동적 툴은 생성 즉시 영구 저장되지 않고 검증과 사용자 승인을 거친다.
- [ ] self-correction은 무한 반복하지 않고 설정된 제한과 실패 원인 분류를 사용한다.
- [ ] CLI에서 LLM 없이도 내장 툴과 문서화된 검증 명령을 실행할 수 있다.

## 12. 나중에 다룰 한계

- subprocess 격리만으로는 파일 시스템 권한을 완전히 제어할 수 없다. 제한 유저, 컨테이너, seccomp 같은 더 강한 샌드박스는 별도 설계가 필요하다.
- 키워드 검색은 툴 수가 늘면 정확도가 떨어질 수 있다. 의존성 비용과 성능을 비교한 뒤 경량 임베딩 또는 외부 색인을 검토한다.
- 다중 에이전트 구조는 초기 구현 범위가 아니다. 실패 귀인과 툴 라이브러리 관리가 안정화된 뒤 Coder, Executor, Librarian 역할 분리를 검토한다.
- 메모리 요약은 정보 손실 위험이 있으므로, 승인/거부/실패 이벤트 같은 안전 관련 정보는 원본 이벤트로 유지해야 한다.
