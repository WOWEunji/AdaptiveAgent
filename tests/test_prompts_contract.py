"""Prompt template contract tests.

자유 영역(영문/한국어 본문 표현)은 검증하지 않는다. 보호하는 계약은:
1. 모든 ``{slot}`` 자리표시자가 치환 가능해야 한다 (loader가 KeyError로 알림).
2. 렌더 후 결과물에 ``{slot}`` 패턴이 남지 않는다 (값 안에 우연히 들어간 패턴까지 검출).
3. 새 변수가 추가되더라도 자동으로 검증된다 (정규식으로 슬롯을 동적으로 수집).
"""

from __future__ import annotations

import re
import unittest
from importlib import resources

from adaptive_agent.prompts import PromptLoader


_SLOT_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _list_default_prompt_files() -> list[str]:
    package = resources.files("adaptive_agent.prompts.default")
    return sorted(
        entry.name
        for entry in package.iterdir()
        if entry.name.endswith(".txt")
    )


def _placeholder_value(slot_name: str) -> str:
    return f"<TEST::{slot_name.upper()}>"


class PromptContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.loader = PromptLoader()
        self.prompt_files = _list_default_prompt_files()
        self.assertGreater(len(self.prompt_files), 0, "default 프롬프트가 최소 한 개는 있어야 합니다")

    def test_default_prompt_set_has_expected_role_files(self) -> None:
        names = set(self.prompt_files)
        for required in {"plan.txt", "coder.txt", "critic.txt", "correction.txt"}:
            self.assertIn(required, names, f"역할 프롬프트가 누락됨: {required}")

    def test_every_prompt_renders_without_unfilled_slots(self) -> None:
        for filename in self.prompt_files:
            with self.subTest(prompt=filename):
                template = self.loader.load(filename)
                slots = sorted(set(_SLOT_PATTERN.findall(template)))
                values = {slot: _placeholder_value(slot) for slot in slots}

                rendered = self.loader.render(filename, **values)

                self.assertNotRegex(
                    rendered,
                    _SLOT_PATTERN.pattern,
                    f"{filename} 렌더 결과에 미치환 자리표시자가 남았습니다",
                )
                for slot in slots:
                    self.assertIn(_placeholder_value(slot), rendered, f"{filename}: {slot} 값이 본문에 들어가지 않음")

    def test_render_with_missing_value_raises_keyerror(self) -> None:
        for filename in self.prompt_files:
            with self.subTest(prompt=filename):
                template = self.loader.load(filename)
                slots = sorted(set(_SLOT_PATTERN.findall(template)))
                if not slots:
                    continue
                missing_one = {slot: _placeholder_value(slot) for slot in slots[1:]}
                with self.assertRaises(KeyError):
                    self.loader.render(filename, **missing_one)

    def test_render_value_carrying_slot_pattern_is_replaced_not_re_expanded(self) -> None:
        # 사용자 task 등 외부 입력에 {slot}처럼 보이는 문자열이 들어와도
        # 두 번째 패스 치환이 일어나면 안 된다 (loader는 단일 치환).
        for filename in self.prompt_files:
            with self.subTest(prompt=filename):
                template = self.loader.load(filename)
                slots = sorted(set(_SLOT_PATTERN.findall(template)))
                if not slots:
                    continue
                values = {slot: _placeholder_value(slot) for slot in slots}
                # 첫 슬롯 값에 다른 슬롯 패턴 텍스트를 심는다
                injected_slot = "{" + slots[0] + "}"
                values[slots[0]] = f"raw value containing {injected_slot}"
                rendered = self.loader.render(filename, **values)
                # 우리가 심은 raw 슬롯 텍스트 자체는 본문에 그대로 보존되어야 한다
                self.assertIn(injected_slot, rendered)


if __name__ == "__main__":
    unittest.main()
