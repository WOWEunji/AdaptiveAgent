"""File-backed prompt template loading."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources


_SLOT_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class PromptLoader:
    """패키지 내부 프롬프트 파일을 읽어 동적 슬롯을 렌더링합니다."""

    prompt_set: str = "default"

    def render(self, template_name: str, **values: object) -> str:
        """지정한 프롬프트 템플릿을 읽고 `{slot}` 값을 치환합니다."""

        template = self.load(template_name)
        missing = sorted(set(_SLOT_PATTERN.findall(template)) - set(values))
        if missing:
            joined = ", ".join(missing)
            raise KeyError(f"Prompt template '{template_name}' is missing values for: {joined}")

        rendered = template
        for key, value in values.items():
            rendered = rendered.replace("{" + key + "}", str(value))
        return rendered

    def load(self, template_name: str) -> str:
        """프롬프트 세트에서 UTF-8 텍스트 템플릿을 로드합니다."""

        if "/" in template_name or "\\" in template_name:
            raise ValueError("template_name must be a file name, not a path")
        package = f"adaptive_agent.prompts.{self.prompt_set}"
        try:
            return resources.files(package).joinpath(template_name).read_text(encoding="utf-8")
        except ModuleNotFoundError as exc:
            raise FileNotFoundError(f"Prompt set not found: {self.prompt_set}") from exc
