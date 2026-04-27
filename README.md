# AdaptiveAgent

An adaptive AI agent system that dynamically creates and executes tools based on natural language tasks, with human-in-the-loop interactions.

## Features
- Dynamic tool generation and execution
- CLI-based interface
- Support for multiple LLMs (Ollama local, OpenAI, Anthropic)
- Tool library management and self-correction
- Bilingual support (English/Korean)

## Setup
1. Install dependencies: `pip install -r requirements.txt`
2. Set up Ollama: `curl -fsSL https://ollama.ai/install.sh | sh`
3. Pull a lightweight model (for Codespace limits): `ollama pull qwen2.5:1.5b` (or phi3:3.8b, gemma:2b)
   - Alternatives: qwen2.5:3b (~2GB), avoid larger models like qwen2.5:7b or qwen3.5:9b due to size.
4. Add API keys to `.env` file (use free tiers for OpenAI, Grok, etc.).

## Testing LLMs
Run: `python llm_test.py <llm_type> "<query>"`

Examples:
- `python llm_test.py ollama "Hello, test message"`
- `python llm_test.py openai "Generate a Python function to sum a list"`
- `python llm_test.py grok "Explain adaptive agents"`
- `python llm_test.py huggingface "What is AI?"`

For free users: Start with Ollama local models or free API tiers (Grok has generous free usage).

## Usage
Implement your adaptive agent logic in Python, integrating the LLM tests for dynamic tool creation.
AdaptiveAIAgent

## Validation Scenarios

AdaptiveAgent의 기본 동작 검증은 [AAVS: Adaptive Agent Validation Scenarios](docs/adaptive_agent_validation_scenarios.md)를 기준으로 수행한다.

이 시나리오 세트는 mock 없이 실제 LLM provider와 실행 환경을 사용해 다음 흐름을 확인한다.

- 자연어 작업 분석
- 동적 툴 생성 및 실행
- 툴 실행 오류 관찰, 자가 수정, 재실행
- 모호한 사용자 요청에 대한 추가 입력 요청
- 생성 툴 저장 동의와 거부 처리
- 저장된 툴 재사용과 중복 생성 방지

#Todo

실행 방법 (로컬 환경 설정 및 LLM 연동 설정 포함)

시스템의 핵심 설계 사항 및 아키텍처 결정 배경

현재 구현된 구조의 한계점 및 향후 개선 가능 방향
