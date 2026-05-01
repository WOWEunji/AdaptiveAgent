"""File-backed prompt template loading."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources


_SLOT_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class PromptLoader:
    """Package-resource prompt loader with named slot rendering."""

    prompt_set: str = "default"

    def render(self, template_name: str, **values: object) -> str:
        """Render a prompt template by replacing `{slot}` values."""

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
        """Load a UTF-8 prompt template from the configured prompt set."""

        if "/" in template_name or "\\" in template_name:
            raise ValueError("template_name must be a file name, not a path")
        package = f"adaptive_agent.prompts.{self.prompt_set}"
        try:
            return resources.files(package).joinpath(template_name).read_text(encoding="utf-8")
        except ModuleNotFoundError as exc:
            raise FileNotFoundError(f"Prompt set not found: {self.prompt_set}") from exc
