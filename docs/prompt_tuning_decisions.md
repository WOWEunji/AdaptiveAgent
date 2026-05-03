# 프롬프트 튜닝 결정 사항

작성일: 2026-05-03

---

## 타겟 모델 (현행)

| 모델 | 프로바이더 | API |
|------|:----:|:----:|
| gpt-5-nano   | OpenAI | Responses API |
| gpt-5.4-mini | OpenAI | Responses API |
| qwen3.5:2b   | Ollama | REST |
| qwen3.5:4b   | Ollama | REST |

> gpt-4o-mini, gpt-5-mini는 더 이상 타겟 모델이 아님.

---

## 배경 — AAVS 평가 결과 (아키텍처 리팩터 후, 16개 시나리오 기준)

| 모델 | 점수 | 비고 |
|------|:----:|:----:|
| gpt-4o-mini  | 15/16 (94%) | AAVS-009만 실패 (모델 한계) |
| qwen3.5:2b   | 미측정 (재측정 필요) | — |
| qwen3.5:4b   | 미측정 (재측정 필요) | — |
| gpt-5-nano   | 미측정 (재측정 필요) | — |
| gpt-5.4-mini | 미측정 (재측정 필요) | — |

---

## 확인된 실패 유형 분류

### A. 형식 오류 (Format Error)

| 시나리오 | 모델 | 증상 | 원인 |
|---------|------|------|------|
| AAVS-001 | qwen 전체 | plan_validation_failed | JSON plan이 토큰 한도 초과로 truncation → `}` 누락 |
| AAVS-001/003B/014/015 | OpenAI 4o-mini/4.1 | plan_validation_failed | LLM이 `\'` (Python escape) JSON 안에 생성 → json.loads 실패 |
| AAVS-011/012 (4o-mini) | OpenAI 4o-mini | plan_validation_failed | `"arguments":[{...}]` (배열) 대신 객체 생성 |

### B. 논리 오류 (Logic Error)

| 시나리오 | 모델 | 증상 | 원인 |
|---------|------|------|------|
| AAVS-007 | qwen 2b/4b/7b/14b | tool_error exit=1 | JSON 배열 데이터를 CSV 파싱으로 처리 (학습 편향) |
| AAVS-011 | gpt-4o-mini | stdout_contains 실패 | 카테고리별 평균이 아닌 전체 평균 계산 (프롬프트 해석 오류) |
| AAVS-013 | qwen 전체 | llm_error: timed out | 복잡한 중첩 JSON 프롬프트에서 Ollama HTTP 타임아웃 |

### C. 도구 오류 (Tool Error)

| 시나리오 | 모델 | 증상 | 원인 |
|---------|------|------|------|
| AAVS-014/015 | 전체 | tool_error exit=1 | tool_create 생성 코드 버그: `[v for r in data]` (v 미정의) |
| AAVS-014/015 | 전체 | "이미 생성된 툴" | self-correction이 tool_create 반복 호출 (루프 버그) |

---

## 적용된 수정 이력

### 시스템 수준 수정 (코드)
1. `_normalize_json_control_chars`: `\'` 등 비표준 JSON escape 제거
2. `_repair_truncated_json`: suffix 확장 (`"}}`, `"}}}`, `")}}` 등) + full text fallback
3. `_normalize_plan`: `arguments` 배열→객체 정규화 (`[{...}]` → `{...}`)
4. `tool_create`: AST 기반 comprehension 변수 바인딩 검사
5. `OLLAMA_NUM_PREDICT`: 1024 → 4096 (토큰 truncation 방지)
6. `OLLAMA_TIMEOUT_SECONDS`: 60 → 120 (복잡 프롬프트 타임아웃 방지)

### 프롬프트 수정
1. `plan.txt`: 파서-포맷 매칭 규칙 ("JSON 배열이면 json.loads, CSV면 csv 모듈")
2. `coder.txt`: 파일 I/O 금지, 파서 매칭, comprehension 바인딩, run() 자기완결 규칙
3. `correction.txt`: PARSER MISMATCH 규칙, TOOL ALREADY EXISTS 규칙

---

## 다음 단계 — 프롬프트 튜닝 방법론

아래 순서를 따른다. **한 번에 하나만 수정하고 A/B 테스트 실시.**

### 1단계: 실패 유형 재분류 (각 run 후)
- raw output 로그에서 A/B/C 판별
- plan_validation_failed = A (형식)
- 잘못된 계산/선택 = B (논리)  
- exit code 1 / NameError = C (도구)

### 2단계: A(형식 오류) → Few-shot 보강 우선순위
- **AAVS-007** (qwen CSV 편향): plan.txt에 JSON 입력 → json.loads 사용 예시 2개 추가
  - 추가 예시 1: `[{"name":"Alice","score":88}]` → `json.loads` 코드
  - 추가 예시 2: CSV 헤더 행 → `csv.DictReader` 코드
- **AAVS-014/015** (tool 코드 버그): coder.txt에 올바른 `run()` 예시 1개

### 3단계: B(논리 오류) → Thought 점검
- AAVS-011 (4o-mini 평균 계산): plan.txt에 "group-by 연산은 카테고리별로 분리" 명시
- OLLAMA_THINK=true 옵션 테스트 (qwen3.5:2b/4b 전용)

### 4단계: C(도구 오류) → 도구 description 재작성
- `tool_create` description에 "언제 써야/쓰면 안 되는지" 추가
- correction.txt에 COMPREHENSION BINDING 패턴 예시 추가

### 5단계: A/B 테스트 방법
```
기준: AAVS 13개 시나리오 × 3회 실행 → 39회 결과의 통과율
변수: 프롬프트 1개 요소 변경
비교: 변경 전 vs 변경 후 통과율
기준선: gpt-4o-mini 9/13, qwen3.5:2b 8/13
```

---

## 잔여 미해결 (모델 수준 한계)
- AAVS-007 (qwen 전체): JSON→CSV 학습 편향. Few-shot 없이 해결 어려움
- AAVS-013 (qwen 전체): Ollama 추론 시간 한계. 더 빠른 모델 필요
- AAVS-014/015 (전체): tool_validate + correction 루프 구조적 개선 필요
