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

#Todo

실행 방법 (로컬 환경 설정 및 LLM 연동 설정 포함)

시스템의 핵심 설계 사항 및 아키텍처 결정 배경

현재 구현된 구조의 한계점 및 향후 개선 가능 방향