# AdaptiveAgent References

## 툴 생성 및 스킬 라이브러리 관련 주요 논문

### 1. SkillX: Automatically Constructing Skill Knowledge Bases for Agents (2026년 4월)
- **핵심 내용**: LLM Agent를 위한 플러그 앤 플레이 형태의 '스킬 지식 기반'을 자동으로 구축하는 프레임워크.
- **참고 포인트**: 단순히 코드만 저장하는 것이 아니라, 툴을 '계획 스킬', '기능 스킬', '원자적 스킬' 등 다층적 구조로 분류하여 저장하고 재사용하는 아키텍처를 제시. 생성된 툴을 이후 세션에서 재사용하는 구조에 적합.

### 2. EvolveTool-Bench: Evaluating the Quality of LLM-Generated Tool Libraries as Software Artifacts (2026년 4월)
- **핵심 내용**: LLM이 스스로 생성한 툴 라이브러리를 단순한 블랙박스가 아닌 하나의 '소프트웨어 아티팩트'로 취급하고 검증하는 방법론.
- **참고 포인트**: Agent가 툴을 무분별하게 생성하게 두면 라이브러리에 중복된 툴이 쌓이거나 기존 툴이 충돌하는 회귀 문제가 발생. 툴의 재사용성, 중복성, 구성 성공률, 안전성 등을 검증하는 지표를 제공하여 신뢰성 있는 장기 메모리 구축에 필수적.

### 3. ToolMaker: Turn GitHub Repositories into LLM Tools (ACL 2025)
- **핵심 내용**: GitHub 코드 리포지토리나 과학 논문의 코드를 자율적으로 LLM이 사용할 수 있는 툴로 변환하는 Agentic Framework.
- **참고 포인트**: 초기 계획에 따라 종속성을 스스로 설치하고, 에러를 진단하고 수정하는 강력한 Closed-loop self-correction 메커니즘을 보여줌. 툴 생성 후 자체적으로 단위 테스트를 거치는 실행 방식에 대한 영감 제공.

### 4. ToolLibGen: Scalable Automatic Tool Creation and Aggregation for LLM Reasoning (2025년 10월)
- **핵심 내용**: 자연어 추론(Chain-of-Thought) 과정에서 반복적으로 등장하는 결정론적이고 알고리즘적인 단계들을 추려내어 재사용 가능한 파이썬 함수(툴)로 자동 생성하고 집계하는 방법.
- **참고 포인트**: 처음에는 자연어 계획만으로 문제를 풀게 한 뒤, 그 과정에서 패턴을 찾아 툴로 모듈화. Built-in 툴을 최소화하고 실행 중에 필요한 툴을 동적으로 확장하는 로직 설계에 유용.

## 추가로 검토해야 할 최신 연구 및 표준 (2025~2026 트렌드)

### 1. Model Context Protocol (MCP) Specification (오픈 표준, 2024 말 ~ 2026 핵심 트렌드)
- **핵심 내용**: Anthropic 등이 주도하여 만든 오픈 소스 표준으로, AI 모델이 데이터 소스 및 로컬 툴(코드 실행 환경 포함)과 안전하고 표준화된 방식으로 연결되도록 하는 프로토콜.
- **왜 검토해야 하는가**: 스스로 툴을 파이썬 코드로 작성하고 실행하게 만들 때, 생성된 툴의 입출력 규격을 하드코딩하거나 프롬프트로만 통제하면 확장에 한계. 현재 2026년 에이전트 아키텍처의 산업 표준은 MCP를 기반으로 도구와 컨텍스트를 연결하는 것. 시스템 설계 시 API와 도구 간의 인터페이스 규격으로 반드시 참고.

### 2. A Probabilistic Inference Scaling Theory for LLM Self-Correction (EMNLP 2025)
- **핵심 내용**: LLM이 에러 로그를 보고 스스로 코드를 수정(Self-correction)하는 과정에서 정확도가 어떻게 변하는지 확률론적으로 분석한 연구.
- **왜 검토해야 하는가**: Agent가 에러를 보고 무한히 자가 수정을 반복한다고 해서 코드가 반드시 고쳐지지 않음. 오히려 '환각 루프'에 빠져 코드가 더 망가지는 경우 많음. LLM의 자가 수정 능력을 '신뢰도'와 '비판' 능력으로 분해하여, 언제 자가 수정을 멈추어야 하는지, 언제 사람 개입을 요청해야 하는지 임계점을 설정하는 수학적/논리적 기준 제공.

### 3. AgentEvolver: Towards Efficient Self-Evolving Agent System & Bottom-Up Skill Evolution (2025년 발표 논문들)
- **핵심 내용**: 기존의 하향식 작업 분할 체계에서 벗어나, 에이전트가 환경과 상호작용하며 상향식으로 스킬을 진화시키는 프레임워크.
- **왜 검토해야 하는가**: 이후 세션에서도 재사용하는 경우, 스킬 라이브러리에 툴이 수백 개 쌓이면 Agent가 어떤 툴을 꺼내 써야 할지 혼란(Retrieval 성능 저하). 이 연구들은 스킬들이 단순히 리스트업되는 것이 아니라, 경험을 바탕으로 스킬 자체가 스스로 병합되고 최적화되는 아키텍처 제시.

### 4. Which Agent Causes Task Failures and When? On Automated Failure Attribution of LLM Multi-Agent Systems (2025)
- **핵심 내용**: 다중 에이전트 시스템에서 작업 실패(코드 실행 오류 등)가 발생했을 때, 실패의 근본 원인('계획 오류', '코드 생성 오류', '환경 문제' 등)을 자동으로 귀인하는 방법론.
- **왜 검토해야 하는가**: 단일 Agent에게 '계획-코드생성-실행-수정'을 모두 맡기면 컨텍스트 오버플로우 발생하고 성능 급감. 코드를 생성하는 역할(Coder), 샌드박스에서 실행 결과를 검증하는 역할(Reviewer/Executor), 스킬을 저장하고 관리하는 역할(Librarian) 등 Multi-Agent 구조로 역할을 분리해야 시스템이 안정적으로 돌아감.