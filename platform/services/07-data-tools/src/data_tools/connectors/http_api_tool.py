"""A generic, config-driven HTTP connector. This is the reusable mechanism
for "call some internal system" tools (ticketing, CRM lookups, and similar)
— a usecase stands up a new connector by writing a YAML file, not a new
Python class. This component does not implement any AFNI-specific system
connector itself; that would be usecase-owned integration logic, not a
platform mechanism. `tests/fixtures/connectors/example_ticketing.yaml`
stands in for a usecase's own connector config, the same way Prompt
Management's `tests/fixtures/usecase_demo/` stands in for a usecase's own
prompts.
"""
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
import yaml


def _default_http_call(method: str, url: str, headers: dict, timeout: float) -> Any:
    response = requests.request(method, url, headers=headers, timeout=timeout)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return response.text


@dataclass
class HttpApiTool:
    name: str
    description: str
    base_url: str
    path_template: str = ""
    method: str = "GET"
    auth_header_name: str | None = None
    auth_header_env_var: str | None = None
    timeout_seconds: float = 10.0
    http_call: Callable[[str, str, dict, float], Any] = field(
        default=_default_http_call, repr=False
    )

    def invoke(self, **kwargs) -> Any:
        path = self.path_template.format(**kwargs)
        url = self.base_url.rstrip("/") + path

        headers = {}
        if self.auth_header_name and self.auth_header_env_var:
            headers[self.auth_header_name] = os.environ[self.auth_header_env_var]

        return self.http_call(self.method, url, headers, self.timeout_seconds)


def load_connector_file(path: Path) -> HttpApiTool:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return HttpApiTool(
        name=raw["name"],
        description=raw.get("description", ""),
        base_url=raw["base_url"],
        path_template=raw.get("path_template", ""),
        method=raw.get("method", "GET"),
        auth_header_name=raw.get("auth_header_name"),
        auth_header_env_var=raw.get("auth_header_env_var"),
        timeout_seconds=raw.get("timeout_seconds", 10.0),
    )
