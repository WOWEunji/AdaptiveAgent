# 연구·아이디어 보관 (`docs/research/`)

## 프로젝트 안에 두는 게 맞나?

**요약·링크·메모·프로젝트와의 연결**은 저장소 안에 두는 편이 좋다. 클론한 사람과 Cursor 규칙(`reference.md`, 연구 페르소나)이 같은 근거를 본다.

**원문 PDF 대량·저작권 민감 파일**은 Git에 직접 넣지 않는 것을 권장한다. 링크(DOI, arXiv, 사내 링크) + 짧은 인용/요약만 저장소에 둔다. PDF가 꼭 필요하면 Git LFS 또는 저장소 밖(개인 Zotero, 사내 드라이브)을 쓴다.

## 무엇을 어디에 둘지

| 종류 | 권장 위치 |
|------|-----------|
| 논문·기술 자료 **요약·비고·프로젝트 적용 아이디어** | `docs/research/` 아래 마크다운 (아래 네이밍) |
| **큐레이션 목록·한 줄 요약 모음** | 루트 `reference.md` (목차 역할) |
| 요구·검증·기획 문서(이미 있는 것) | `docs/` (예: `requirements_breakdown.md`, `adaptive_agent_validation_scenarios.md`) |
| **구현 확정된 작업 단위** | GitHub **Issue** (+ 브랜치). 메모만 남길 게 아니라 실행할 때 |

## 파일 네이밍 예

- `YYYY-topic-slug.md` — 예: `2026-skillx-skill-knowledge-notes.md`
- 한 파일에: 출처 링크, 3~5줄 요약, **우리 프로젝트에 적용 시 가정·한계**, 열린 질문

## `reference.md`와의 역할 나누기

- `reference.md`: “무엇을 읽을지” **인덱스·우선순위**.
- `docs/research/*.md`: 각 글·주제에 대한 **깊은 메모**. 새 논문을 깊게 읽었으면 요약 파일을 여기 추가하고, `reference.md`에는 항목과 링크만 보강하면 된다.

## 아이디어 스크래치

- 아직 논문 단위가 아니면 같은 폴더에 `ideas-backlog.md` 등 하나 두고 bullet로 적어도 된다. 성숙하면 Issue나 `docs/requirements_breakdown.md` 쪽으로 옮긴다.
