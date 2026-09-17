"""Loads prompt and fragment files from directories supplied by the caller,
then resolves and renders them by name. This is the reusable mechanism: every
usecase's prompt folder is loaded through this exact same code path, which is
what makes onboarding a new usecase's prompts a config change (which
directories to point the registry at) rather than a code change. See
docs/decisions/0006-prompt-management-git-backed-storage.md.
"""
import re
from collections.abc import Iterable
from pathlib import Path

from .loader import load_fragment_file, load_prompt_file
from .types import (
    DuplicatePromptError,
    FragmentNotFoundError,
    FragmentSpec,
    MissingVariableError,
    PromptNotFoundError,
    PromptSpec,
)

_FRAGMENT_TOKEN = re.compile(r"\{\{fragment:(\w+)\}\}")


class PromptRegistry:
    def __init__(
        self,
        prompt_dirs: Iterable[Path] = (),
        fragment_dirs: Iterable[Path] = (),
    ):
        self._prompts: dict[str, PromptSpec] = {}
        self._fragments: dict[str, FragmentSpec] = {}

        for directory in fragment_dirs:
            self._load_fragments(Path(directory))
        for directory in prompt_dirs:
            self._load_prompts(Path(directory))

    def _load_prompts(self, directory: Path) -> None:
        for path in sorted(directory.glob("*.yaml")):
            spec = load_prompt_file(path)
            if spec.name in self._prompts:
                raise DuplicatePromptError(
                    f"Prompt '{spec.name}' loaded from both "
                    f"{self._prompts[spec.name].source_path} and {path}"
                )
            self._prompts[spec.name] = spec

    def _load_fragments(self, directory: Path) -> None:
        for path in sorted(directory.glob("*.yaml")):
            spec = load_fragment_file(path)
            self._fragments[spec.name] = spec

    def resolve(self, name: str) -> PromptSpec:
        try:
            return self._prompts[name]
        except KeyError as exc:
            raise PromptNotFoundError(f"No prompt registered under '{name}'") from exc

    def render(self, name: str, **variables: str) -> str:
        spec = self.resolve(name)
        text = self._expand_fragments(spec.template)

        missing = [v for v in spec.input_variables if v not in variables]
        if missing:
            raise MissingVariableError(
                f"Prompt '{name}' is missing required variable(s): {', '.join(missing)}"
            )

        for var_name in spec.input_variables:
            text = text.replace("{" + var_name + "}", str(variables[var_name]))

        return text

    def _expand_fragments(self, text: str) -> str:
        def _replace(match: "re.Match[str]") -> str:
            fragment_name = match.group(1)
            try:
                fragment = self._fragments[fragment_name]
            except KeyError as exc:
                raise FragmentNotFoundError(
                    f"Template references unknown fragment '{fragment_name}'"
                ) from exc
            return fragment.template

        return _FRAGMENT_TOKEN.sub(_replace, text)

    def list_prompts(self) -> list[str]:
        return sorted(self._prompts)
