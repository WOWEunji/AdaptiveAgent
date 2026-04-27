# AdaptiveAgent

AdaptiveAgent는 자연어 작업을 분석해 내장 툴 또는 LLM으로 처리하는 CLI 기반 에이전트 프로젝트입니다. 현재 뼈대는 에이전트 오케스트레이션, LLM 어댑터, 툴 레지스트리/실행기, 테스트를 분리해 이후 동적 툴 생성과 self-correction 기능을 확장하기 쉽게 구성했습니다.

## 주요 기능

- CLI 기반 실행 진입점: `python -m adaptive_agent`
- Ollama 기반 기본 LLM 어댑터
- LLM 없이도 Codespace에서 검증 가능한 내장 툴(`echo`, `list_files`, `analyze_requirements`)
- 키워드 매칭 기반 내장 툴 레지스트리
- 툴 실행 결과 표준화
- 한국어/영어 작업 입력을 고려한 UTF-8 구조
- `unittest` 기반 기본 회귀 테스트
- `reference.md` 방법론을 반영한 요구사항 분해 문서: `docs/requirements_breakdown.md`

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
    registry.py        # 기본 툴 등록 및 매칭
    executor.py        # 등록 툴 실행기
docs/
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

내장 echo 툴 확인:

```bash
python3 -m adaptive_agent "ping 그대로 응답해줘"
python3 -m adaptive_agent --json "echo hello"
```

Codespace에서 LLM 없이 기능 확인:

```bash
python3 -m adaptive_agent --list-tools
python3 -m adaptive_agent --json "파일 목록 보여줘"
python3 -m adaptive_agent "요구사항 분해 보여줘"
```

Ollama 연결이 가능한 경우:

```bash
python3 -m adaptive_agent "동적 툴 생성 아키텍처를 요약해줘"
```

## 테스트

```bash
python3 -m unittest discover
```

## 설계 방향

- 에이전트 코어(`agent.py`)는 작업 입력, 툴 선택, LLM fallback만 담당합니다.
- LLM 구현은 `LLMClient` 프로토콜 뒤에 숨겨 OpenAI, Anthropic, Grok 등을 추가하기 쉽게 했습니다.
- 툴은 `Tool` 모델로 등록하고 `ToolExecutor`가 표준 결과로 감싸 실행합니다.
- `reference.md`는 구현 코드가 아니라 방법론 참고 문서로 유지합니다.
- 구체 요구사항과 단계별 구현 단위는 `docs/requirements_breakdown.md`에서 관리합니다.
- 향후 동적 툴 생성은 `tools/` 하위에 생성/검증/저장 계층을 추가하는 방식으로 확장할 수 있습니다.

## 현재 한계와 다음 단계

- 현재 동적 툴 생성은 아직 실제 코드 생성/저장까지 구현하지 않았습니다.
- 내장 툴 매칭은 단순 키워드 기반입니다. 이후 LLM 기반 계획/툴 선택기로 교체할 수 있습니다.
- 외부 provider 어댑터는 아직 패키지에 추가하지 않았으며, 현재는 Ollama부터 시작합니다.
- 생성 툴의 안전성 검증, 중복 제거, self-correction 반복 제한 정책은 다음 구현 단계에서 추가해야 합니다.