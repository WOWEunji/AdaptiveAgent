#!/usr/bin/env python3
"""
LLM Integration Test Script for AdaptiveAgent

이 스크립트는 Adaptive AI Agent 시스템에서 다양한 LLM(대형 언어 모델)을 테스트하기 위한 도구입니다.
로컬 모델(Ollama), 상용 API(OpenAI, Anthropic), 무료 API(Grok, Hugging Face)를 지원하여
Agent가 동적으로 툴을 생성하고 실행하기 전에 LLM 연동을 검증할 수 있습니다.

주요 역할:
- LLM API 연결성 테스트
- 응답 품질 검증
- Agent 개발 중 빠른 피드백 제공
- 다중 LLM 비교 테스트

사용법:
    python llm_test.py <llm_type> "<query>"

지원되는 LLM 타입:
- ollama: 로컬 모델 (Ollama 사용, 비용 없음)
- openai: OpenAI GPT 모델 (유료, 무료 티어 가능)
- anthropic: Anthropic Claude 모델 (유료)
- grok: xAI Grok 모델 (무료 티어 제공)
- huggingface: Hugging Face Inference API (무료 티어)

환경 변수(.env 파일):
- OLLAMA_MODEL: 사용할 Ollama 모델 (기본: qwen2.5:1.5b)
- OPENAI_API_KEY: OpenAI API 키
- ANTHROPIC_API_KEY: Anthropic API 키
- GROK_API_KEY: Grok API 키
- HF_API_KEY: Hugging Face API 키

예시:
    python llm_test.py ollama "Hello, test message"
    python llm_test.py openai "Generate Python code for sum function"
"""

import os
import sys
from dotenv import load_dotenv
import ollama
from openai import OpenAI
from anthropic import Anthropic

# Load environment variables
load_dotenv()

def test_ollama(query):
    """
    Ollama를 사용하여 로컬 LLM 테스트.
    
    Args:
        query (str): 테스트할 쿼리
        
    Returns:
        str: LLM 응답 또는 오류 메시지
    """
    model = os.getenv('OLLAMA_MODEL', 'qwen2.5:7b')
    try:
        response = ollama.chat(model=model, messages=[{'role': 'user', 'content': query}])
        return response['message']['content']
    except Exception as e:
        return f"Ollama Error: {e}"

def test_openai(query):
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        return "OpenAI API key not set."
    client = OpenAI(api_key=api_key)
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": query}]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"OpenAI Error: {e}"

def test_grok(query):
    api_key = os.getenv('GROK_API_KEY')
    if not api_key:
        return "Grok API key not set."
    # Assuming Grok API endpoint (xAI)
    import requests
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = {"model": "grok-beta", "messages": [{"role": "user", "content": query}]}
    try:
        response = requests.post("https://api.x.ai/v1/chat/completions", headers=headers, json=data)
        return response.json()['choices'][0]['message']['content']
    except Exception as e:
        return f"Grok Error: {e}"

def test_huggingface(query):
    api_key = os.getenv('HF_API_KEY')
    if not api_key:
        return "Hugging Face API key not set."
    import requests
    headers = {"Authorization": f"Bearer {api_key}"}
    data = {"inputs": query, "parameters": {"max_new_tokens": 100}}
    try:
        response = requests.post("https://api-inference.huggingface.co/models/microsoft/DialoGPT-medium", headers=headers, json=data)
        return response.json()[0]['generated_text']
    except Exception as e:
        return f"Hugging Face Error: {e}"

def main():
    if len(sys.argv) < 3:
        print("Usage: python llm_test.py <llm_type> <query>")
        print("LLM types: ollama, openai, anthropic, grok, huggingface")
        return

    llm_type = sys.argv[1].lower()
    query = ' '.join(sys.argv[2:])

    if llm_type == 'ollama':
        result = test_ollama(query)
    elif llm_type == 'openai':
        result = test_openai(query)
    elif llm_type == 'anthropic':
        result = test_anthropic(query)
    elif llm_type == 'grok':
        result = test_grok(query)
    elif llm_type == 'huggingface':
        result = test_huggingface(query)
    else:
        result = "Unsupported LLM type."

    print(result)

if __name__ == "__main__":
    main()