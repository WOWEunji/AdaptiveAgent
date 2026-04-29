# AdaptiveAgent

AdaptiveAgent는 자연어 작업을 분석해 내장 툴 또는 LLM으로 처리하는 CLI 기반 에이전트 프로젝트입니다. 현재 뼈대는 에이전트 오케스트레이션, LLM 어댑터, 툴 레지스트리/실행기, 테스트를 분리해 이후 동적 툴 생성과 self-correction 기능을 확장하기 쉽게 구성했습니다.

## 주요 기능

- CLI 기반 실행 진입점: `python -m adaptive_agent`
- Ollama 기반 기본 LLM 어댑터
- 명시적 CLI 툴 실행(`--tool`)으로 Codespace에서 검증 가능한 내장 툴(`echo`, `list_files`, `file_list`, `file_read`, `file_write`, `file_patch`, `code_execute`, `shell_run`, `test_run`, `ask_human`, `propose_actions`, `tool_create`, `tool_validate`, `tool_search`, `memory_read`, `memory_write`, `analyze_requirements`)
- 자연어 task는 rule matching 없이 LLM 계획 JSON으로만 처리
- 툴 실행 결과 표준화
- 한국어/영어 작업 입력을 고려한 UTF-8 구조
- `unittest` 기반 기본 회귀 테스트
- `reference.md` 방법론을 반영한 요구사항 분해 문서: `docs/requirements_breakdown.md`
- 프레임워크 독립형 에이전트 기초 설계 문서: `docs/basic_architecture_design.md`

## 프로젝트 구조

```text
adaptive_agent/
  __init__.py          # 공개 패키지 API
  __main__.py          # python -m adaptive_agent 진입점
  agent.py             # 작업 분석 및 실행 오케스트레이션
  cli.py               # CLI 파서와 출력 처리
  config.py            # 환경 변수 기반 설정
  llms/
    base.py            # LLMClient 프로토콜
    factory.py         # provider별 LLM 생성
    ollama.py          # Ollama 어댑터
  tools/
    models.py          # Tool / ToolExecutionResult 모델
    registry.py        # 기본 툴 등록 및 이름 조회
    executor.py        # 등록 툴 실행기
docs/
  basic_architecture_design.md
  requirements_breakdown.md
tests/
  test_agent.py
  test_cli.py
```

## 설치 및 환경 설정

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Validation Scenarios

AdaptiveAgent의 기본 동작 검증은 [AAVS: Adaptive Agent Validation Scenarios](docs/adaptive_agent_validation_scenarios.md)를 기준으로 수행한다.

이 시나리오 세트는 mock 없이 실제 LLM provider와 실행 환경을 사용해 다음 흐름을 확인한다.

- 자연어 작업 분석
- 동적 툴 생성 및 실행
- 툴 실행 오류 관찰, 자가 수정, 재실행
- 모호한 사용자 요청에 대한 추가 입력 요청
- 생성 툴 저장 동의와 거부 처리
- 저장된 툴 재사용과 중복 생성 방지

Ollama를 사용할 경우:

```bash
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull qwen2.5:1.5b
```

`.env` 주요 설정:

```env
ADAPTIVE_AGENT_LLM=ollama
OLLAMA_MODEL=qwen2.5:1.5b
ADAPTIVE_AGENT_WORKSPACE=.
ADAPTIVE_AGENT_MAX_SELF_CORRECTIONS=2
```

## 실행 방법

명시적 내장 echo 툴 확인:

```bash
python3 -m adaptive_agent --tool echo --arg task="echo hello"
python3 -m adaptive_agent --json --tool echo --arg task="echo hello"
```

Codespace에서 LLM 없이 기능 확인:

```bash
python3 -m adaptive_agent --list-tools
python3 -m adaptive_agent --json --tool list_files
python3 -m adaptive_agent --json --tool file_list --arg path=adaptive_agent --arg recursive=true
python3 -m adaptive_agent --json --tool file_read --arg path=README.md
python3 -m adaptive_agent --json --tool code_execute --arg code="print('ok')" --arg expected_output=ok
python3 -m adaptive_agent --json --tool shell_run --arg code="echo ok" --arg expected_stdout_contains=ok
python3 -m adaptive_agent --json --tool test_run --arg command="python3 -m unittest discover"
python3 -m adaptive_agent --tool analyze_requirements
```

Ollama 연결이 가능한 경우:

```bash
python3 -m adaptive_agent "동적 툴 생성 아키텍처를 요약해줘"
```

## 테스트

```bash
python3 -m unittest discover
python3 -m compileall adaptive_agent tests
```

## PR 검증 파이프라인

GitHub Actions의 `PR Validation` 워크플로는 pull request와 `main` push에서 다음을 실행한다.

```bash
python3 -m unittest discover
python3 -m compileall adaptive_agent tests
python3 -m adaptive_agent --list-tools
python3 -m adaptive_agent --json --tool echo --arg task="hello from ci"
python3 -m adaptive_agent --json --tool list_files --arg path=adaptive_agent
python3 -m adaptive_agent --tool analyze_requirements
```

필요하면 GitHub Actions에서 `Run workflow`로 수동 실행할 수 있다. `task`, `tool`, `tool_arg`, `llm_provider`, `llm_model` 입력을 바꿔 PR 브랜치의 CLI 결과를 원격 로그와 Actions Summary에서 확인할 수 있다.

- `llm_provider=ollama`: workflow가 Ollama를 설치하고 지정 모델을 pull한 뒤 자연어 task를 실행한다.
- `llm_provider=openai`: repository secret `OPENAI_API_KEY`가 있을 때 실행한다.
- `llm_provider=gemini`: repository secret `GEMINI_API_KEY` 또는 `GOOGLE_API_KEY`가 있을 때 실행한다.

수동 실행 예:

| 목적 | 입력 |
| --- | --- |
| 내장 툴 원격 확인 | `tool=echo`, `tool_arg=task=hello` |
| Ollama 자연어 실행 | `llm_provider=ollama`, `llm_model=qwen2.5:1.5b`, `task=AdaptiveAgent를 한 문장으로 설명해줘` |
| OpenAI 자연어 실행 | `llm_provider=openai`, `llm_model=gpt-5-nano`, `task=등록된 툴 목록을 요약해줘` |
| Gemini 자연어 실행 | `llm_provider=gemini`, `llm_model=gemini-2.5-flash-lite`, `task=다음 구현 단계를 요약해줘` |

OpenAI/Gemini를 사용할 때는 GitHub 저장소 Settings -> Secrets and variables -> Actions에 실제 API key를 repository secret으로 추가해야 한다. placeholder key는 클라이언트 초기화 단계에서 거부된다. OpenAI 모델명은 실제 계정에서 사용 가능한 값을 넣어야 하며, 예를 들어 `gpt-5-nano` 또는 `gpt-4o-mini`처럼 존재하는 모델을 사용한다. 존재하지 않는 모델명(예: 임의의 `gpt-5.2-nano`)은 `model_not_found`로 실패한다.

OpenAI/Gemini/Ollama 연결만 빠르게 확인하고 싶으면 Actions의 **Manual LLM Check** workflow를 사용한다. 이 workflow는 수동 실행 전용이며, 실패해도 traceback 대신 JSON 오류와 Actions Summary를 남긴다.

AAVS 시나리오 기반으로 OpenAI 또는 Ollama가 적절한 툴 호출과 구조화 데이터 파싱 코드를 생성하는지 확인하려면 같은 workflow에서 `validation_suite=aavs`를 선택한다. OpenAI는 repository secret `OPENAI_API_KEY`를 사용하고, Ollama는 workflow runner에서 Ollama를 설치한 뒤 선택 모델을 pull한다.

로컬에서 provider가 준비된 경우에는 아래처럼 직접 실행할 수 있다.

```bash
python scripts/aavs_validate.py --provider openai --model gpt-5-nano --output-dir aavs-results-openai
python scripts/aavs_validate.py --provider ollama --model qwen2.5:1.5b --output-dir aavs-results-ollama
```

검증 프롬프트는 영어로 작성되어 있으며, 특정 정답만 맞히도록 유도하지 않고 JSON/CSV 같은 구조화 데이터는 표준 파서를 사용한 실행 가능한 Python 코드로 처리하도록 일반 원칙을 확인한다.

현재 자동 하네스는 AAVS-001, AAVS-003A/B, AAVS-006을 대상으로 한다. AAVS-002(self-correction), AAVS-004(저장 동의 y/n), AAVS-005(저장 툴 재사용)는 에이전트 루프와 툴 저장 정책이 확장된 뒤 별도 자동 검증으로 추가해야 한다. 또한 현재 Agent는 툴 실행 후 자연어 최종 답변을 다시 합성하지 않고 raw tool output을 반환하므로, 하네스의 `final_response_scope` 필드는 `raw_tool_output`으로 기록된다.

## Codespace CLI 검증 체크리스트

LLM 없이 검증:

```bash
python3 -m adaptive_agent --list-tools
python3 -m adaptive_agent --json --tool echo --arg task="echo hello"
python3 -m adaptive_agent --json --tool list_files
python3 -m adaptive_agent --tool analyze_requirements
```

Ollama 설치 후 LLM fallback 검증:

```bash
ollama pull qwen2.5:1.5b
python3 -m adaptive_agent "AdaptiveAgent의 다음 구현 단계를 요약해줘"
```

## 설계 방향

- 에이전트 코어(`agent.py`)는 사용자 원문 task를 LLM에 전달하고, LLM이 반환한 JSON 계획만 실행합니다.
- 자연어 task는 원문 보존을 위해 하나의 따옴표 인자로 전달해야 합니다.
- LLM 구현은 `LLMClient` 프로토콜 뒤에 숨겨 OpenAI, Anthropic, Grok 등을 추가하기 쉽게 했습니다.
- 툴은 `Tool` 모델로 등록하고 `ToolExecutor`가 표준 결과로 감싸 실행합니다.
- `code_execute`, `shell_run`, `test_run`, `tool_validate`는 `LocalSandboxBackend`를 통해 별도 프로세스에서 실행하며 stdout/stderr/exit code/timeout/기대값 판정을 구조화해 반환합니다.
- `LocalSandboxBackend`는 실제 워크스페이스 절대경로, 민감 절대경로, 파괴적 shell 패턴, 워크스페이스 symlink 복사를 로컬 정책으로 차단합니다.
- `test_run`은 현재 워크스페이스 복사본에서 프로젝트 명령을 실행해 실제 작업 디렉터리에 테스트 산출물을 남기지 않습니다.
- `ask_human`과 `propose_actions`는 실제 승인을 대신하지 않고 `pending_human_input` 또는 `approval_required` 상태를 반환해 상위 루프가 멈출 수 있게 합니다.
- `file_patch`는 전체 덮어쓰기 대신 단일 텍스트 치환과 dry-run diff를 지원합니다.
- `tool_create`는 생성 툴 코드를 `.adaptive_agent/tools`에 저장하고, `tool_validate`가 `run(arguments)` 샘플 실행으로 검증합니다.
- `memory_read`/`memory_write`는 사용자 승인 후 유지할 로컬 메모리 값을 `.adaptive_agent/memory`에 JSON으로 저장합니다.
- 사용자 입력 정규화와 키워드/rule matching은 사용하지 않습니다.
- `reference.md`는 구현 코드가 아니라 방법론 참고 문서로 유지합니다.
- 구체 요구사항과 단계별 구현 단위는 `docs/requirements_breakdown.md`에서 관리합니다.
- 향후 동적 툴 생성은 `tools/` 하위에 생성/검증/저장 계층을 추가하는 방식으로 확장할 수 있습니다.

## 현재 한계와 다음 단계

- 현재 샌드박스는 표준 라이브러리 기반 `LocalSandboxBackend`입니다. 별도 프로세스, 최소 환경 변수, 임시 디렉터리/워크스페이스 복사본 격리와 로컬 정책 차단을 제공하지만 컨테이너/VM 수준의 완전 격리는 아닙니다.
- 자연어 task 처리는 LLM 연결이 필요합니다. LLM 없이 검증할 때는 `--tool`로 내장 툴을 명시 실행합니다.
- 외부 provider 어댑터는 아직 패키지에 추가하지 않았으며, 현재는 Ollama부터 시작합니다.
- 생성 툴의 보안 정책, 중복 제거, self-correction 반복 제한 정책은 다음 구현 단계에서 강화해야 합니다.
