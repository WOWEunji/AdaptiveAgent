"""Interactive test scenario definitions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class InteractiveScenario:
    """A single interactive scenario with scripted turns and HITL responses.

    hitl_responses keys: "approval_required" | "ask_human"
    hitl_responses values: text to inject automatically.
    """

    scenario_id: str
    title: str
    turns: tuple[str, ...]
    hitl_responses: dict[str, str] = field(default_factory=dict)


INTERACTIVE_SCENARIOS: tuple[InteractiveScenario, ...] = (
    InteractiveScenario(
        scenario_id="S-01",
        title="멀티턴 + 스킬 저장",
        turns=(
            '아래 JSON 데이터에서 체력(hp)이 100 이상인 몬스터의 이름과 평균 hp를 알려줘. [{"name":"Goblin","hp":80},{"name":"Orc","hp":150},{"name":"Dragon","hp":300}]',
            "지금 스킬 저장해줘",
        ),
    ),
    InteractiveScenario(
        scenario_id="S-02",
        title="스킬 재사용 확인",
        turns=(
            '아래 JSON 데이터에서 체력(hp)이 100 이상인 몬스터의 이름과 평균 hp를 알려줘. [{"name":"Goblin","hp":80},{"name":"Orc","hp":150},{"name":"Dragon","hp":300}]',
            '이들 중 가장 hp가 가장 높은 몬스터의 이름은?'
        ),
        # S-01과 동일 workspace 사용 (runner가 처리)
    ),
    InteractiveScenario(
        scenario_id="S-03",
        title="잘못된 코드 자가 수정",
        turns=(
            "'https://www.domain.google.naver.com' 웹 사이트에서 본문 정보를 가져올 수 있는 크롤링 코드를 작성해줘 20줄 이하로",
        ),
    ),
    InteractiveScenario(
        scenario_id="S-04",
        title="부족한 데이터 → ask_human → 재시도",
        turns=(
            "데이터 정리해줘",
            '"ask_human": "이름,나이,부서,입사일\n김철수,30,영업팀,2020-01-15\n이영희,28,개발팀,2021-03-10\n박민준,35,인사팀,2019-11-20"',
            "아니"
        ),
    ),
    InteractiveScenario(
        scenario_id="S-05",
        title="멀티턴 조건 추가",
        turns=(
            '아래 데이터에서 평균 score 알려줘. [{"name":"Alice","score":92},{"name":"Bob","score":75},{"name":"Charlie","score":88},{"name":"Dan","score":60}]',
            "이들중 이름이 C로 시작하는 사람만 다시 필터해줘",
        ),
    ),
    InteractiveScenario(
        scenario_id="S-06",
        title="스킬 조회 및 삭제 ",
        turns=(
            '등록된 스킬 이름 모두다 보여줘',
            '등록된 스킬들 모두 다 삭제해줘'
        ),
    )
)
