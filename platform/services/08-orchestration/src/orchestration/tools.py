"""Tool interface. No concrete tools exist yet — component 07 (Data & Tools)
implements search_knowledge, query_sql, extract_document, get_record against
this interface. Steps can reference tools by name; an empty registry is a
valid, expected state until then.
"""
from typing import Any, Protocol


class Tool(Protocol):
    name: str
    description: str

    def invoke(self, **kwargs) -> Any:
        ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"No tool registered under '{name}'") from exc

    def __contains__(self, name: str) -> bool:
        return name in self._tools
