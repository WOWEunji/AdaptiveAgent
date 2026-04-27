# AdaptiveAgent 요구사항 분석 및 기능 분해

이 문서는 `reference.md`를 방법론 참고 자료로 두고, Codespace CLI 검증이 가능한 형태로 AdaptiveAgent의 요구사항을 분해한 결과입니다.

## 1. 제품 목표

AdaptiveAgent는 사용자의 자연어 작업을 받아 다음 순서로 처리하는 CLI 기반 에이전트입니다.

1. 작업 의도를 분석한다.
2. 이미 등록된 툴로 처리 가능한지 확인한다.
3. 필요한 경우 LLM에게 계획 또는 응답 생성을 요청한다.
4. 향후에는 반복 작업을 재사용 가능한 툴로 생성하고 검증한 뒤 라이브러리에 저장한다.

## 2. 핵심 사용자 시나리오

### 2.1 Codespace에서 빠른 기능 검증

- 사용자는 저장소에 접속한 뒤 `python3 -m adaptive_agent ...` 명령으로 CLI가 동작하는지 확인한다.
- Ollama나 외부 API 키가 없어도 기본 내장 툴은 동작해야 한다.
- JSON 출력으로 자동 검증할 수 있어야 한다.

검증 예:

```bash
python3 -m adaptive_agent --list-tools
python3 -m adaptive_agent --json "echo hello"
python3 -m adaptive_agent --json "파일 목록 보여줘"
```

### 2.2 LLM 연결 검증

- Ollama 설치 및 모델 pull 이후 자연어 작업을 LLM으로 전달할 수 있어야 한다.
- 외부 provider는 패키지 구조가 준비된 뒤 어댑터를 추가한다.

검증 예:

```bash
python3 -m adaptive_agent "동적 툴 생성 계획을 요약해줘"
```

### 2.3 동적 툴 생성 준비

- 현재 단계에서는 실제 코드 생성보다 안전한 실행 계약을 먼저 둔다.
- 생성 툴은 이름, 설명, 키워드, 입력 스키마, 실행 결과, 검증 상태를 가져야 한다.
- 생성된 툴은 중복/안전성 검사를 통과한 뒤 저장소에 보관한다.

## 3. 기능 분해

### 3.1 CLI 계층

책임:

- 사용자 입력 파싱
- 언어 및 provider 옵션 반영
- 등록 툴 목록 출력
- 일반 텍스트 또는 JSON 결과 출력

현재 구현:

- `python3 -m adaptive_agent "<task>"`
- `--json`
- `--language`
- `--llm`
- `--list-tools`

다음 확장:

- `--save-tool`
- `--dry-run`
- `--approve` 또는 human-in-the-loop 승인 플래그

### 3.2 Agent Orchestration 계층

책임:

- 빈 입력 처리
- 작업 분석 결과 기록
- 툴 매칭
- 툴 실행
- LLM fallback

현재 구현:

- 키워드 기반 내장 툴 매칭
- 실행 결과에 `mode`, `tool_name`, `error` 포함

다음 확장:

- Planner 모듈 분리
- 실행 실패 원인 분류
- self-correction 반복 제한

### 3.3 Tool 계층

책임:

- 툴 정의 표준화
- 툴 검색/등록
- 실행 결과 표준화
- workspace 경계 밖 접근 차단

현재 구현:

- `echo`
- `analyze_requirements`
- `list_tools`
- `list_files`

다음 확장:

- `read_text`
- generated tool loader
- tool metadata 저장
- validation/deduplication

### 3.4 LLM Adapter 계층

책임:

- provider별 API 차이를 숨긴다.
- Agent는 `LLMClient.complete(prompt)`만 호출한다.

현재 구현:

- Ollama 어댑터

다음 확장:

- OpenAI
- Anthropic
- Grok/xAI
- Hugging Face

### 3.5 Tool Library 계층

책임:

- 생성 툴 저장
- 중복 탐지
- 안전성 검사
- 재사용성 평가

현재 구현:

- 디렉터리 설정값만 존재

다음 확장:

- `.adaptive_agent/tools/manifest.json`
- 툴 품질 지표
- 실패 이력과 self-correction 이력 저장

## 4. `reference.md` 방법론 반영

### SkillX

- 툴을 단순 파일이 아니라 계획 스킬, 기능 스킬, 원자 스킬로 분류한다.
- 초기 구조에서는 `Tool` 모델의 메타데이터를 확장할 수 있게 둔다.

### EvolveTool-Bench

- 생성 툴은 코드 생성 직후 바로 저장하지 않는다.
- 최소 단위 테스트, 입력/출력 계약, 중복성 검사를 통과한 뒤 저장한다.

### ToolMaker

- 생성 -> 실행 -> 실패 분석 -> 수정의 closed-loop를 둔다.
- 단, 무한 수정 루프를 막기 위해 `ADAPTIVE_AGENT_MAX_SELF_CORRECTIONS`를 설정으로 둔다.

### ToolLibGen

- 반복적으로 등장하는 결정론적 작업을 툴 후보로 승격한다.
- 초기 내장 툴은 최소화하고 실제 사용 패턴을 기반으로 확장한다.

### MCP

- 장기적으로 툴 입출력 계약을 MCP 호환 형태로 정리한다.
- 현재는 내부 `Tool` 모델로 경량 계약을 먼저 둔다.

### Failure Attribution / Multi-Agent

- 이후 Coder, Executor, Librarian 역할을 분리한다.
- 현재 패키지 분리는 이 역할 분리를 수용하기 위한 기초 구조다.

## 5. 정리한 불필요 요소

- 기존 `llm_test.py`는 패키지 CLI와 중복되고 `test_anthropic` 미정의 상태라 제거 대상이다.
- 외부 provider 의존성은 아직 패키지에서 사용하지 않으므로 초기 requirements에서 제외한다.
- LLM provider 추가 시점에 필요한 의존성을 다시 추가한다.

## 6. Codespace 검증 체크리스트

```bash
python3 -m unittest discover
python3 -m compileall adaptive_agent tests
python3 -m adaptive_agent --list-tools
python3 -m adaptive_agent --json "echo hello"
python3 -m adaptive_agent --json "파일 목록 보여줘"
```

Ollama 검증:

```bash
ollama pull qwen2.5:1.5b
python3 -m adaptive_agent "AdaptiveAgent의 다음 구현 단계를 요약해줘"
```

