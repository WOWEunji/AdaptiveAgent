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
- 역할별 Agent는 `nodes/` 계약을 따른다.

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

### 2026-05-02: 생성 툴은 사용자 승인 후에만 manifest에 등록한다

- `tool_create`는 생성 파일과 개별 metadata만 만든다.
- `tool_validate`는 샌드박스 검증 결과만 기록한다.
- `tool_approve`가 사용자 승인 후 `.adaptive_agent/tools/manifest.json`에 등록한다.
- `tool_search`는 승인되어 manifest에 들어간 생성 툴만 검색 후보로 본다.

### 2026-05-02: 특정 작업용 정규식/하드코딩보다 큰 경계를 우선한다

- 임시 intent 분류 정규식을 늘리는 방식은 피한다.
- 새 동작은 가능한 한 `AgentState`, `StateMachineRouter`, `nodes`, `prompts`, `SkillCatalog` 경계 안에 배치한다.
- 구조화된 JSON action 계약을 우선 확장한다.

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

- Coder/Critic을 실제 LLM 호출 노드로 언제 승격할 것인가?
- `AgentState.next_node`를 `Literal`로 유지할지, 런타임 검증 가능한 `Enum`으로 바꿀 것인가?
- SkillCatalog에 embedding vector를 직접 저장할지, 별도 index 참조만 둘 것인가?
- 승인/거부 HITL 흐름을 CLI에서 어떻게 재개 가능하게 만들 것인가?

## 다음 검토 후보

- PlanNode 이후 Coder/Critic 실행 루프 연결
- `use_tool`, `create_tool`, `approve_tool`, `final_answer` 계약의 provider별 안정성 확인
- `manifest.json` metadata schema와 migration 정책
- AAVS-004, AAVS-005 기반 저장 승인/재사용 검증
