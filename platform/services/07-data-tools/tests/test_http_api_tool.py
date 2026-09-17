import os
from pathlib import Path

from data_tools.connectors.http_api_tool import HttpApiTool, load_connector_file

_FIXTURE = Path(__file__).parent / "fixtures" / "connectors" / "example_ticketing.yaml"


def _fake_http_call(method, url, headers, timeout):
    return {"method": method, "url": url, "headers": headers, "timeout": timeout}


def test_load_connector_file_builds_tool():
    tool = load_connector_file(_FIXTURE)
    assert tool.name == "get_ticket"
    assert tool.base_url == "https://example-ticketing.invalid"


def test_invoke_fills_path_template_and_auth_header():
    os.environ["EXAMPLE_TICKETING_TOKEN"] = "test-token-123"
    tool = load_connector_file(_FIXTURE)
    tool.http_call = _fake_http_call

    result = tool.invoke(ticket_id="T-42")

    assert result["url"] == "https://example-ticketing.invalid/tickets/T-42"
    assert result["headers"]["Authorization"] == "test-token-123"


def test_tool_can_be_built_directly_without_a_file():
    tool = HttpApiTool(
        name="inline_example",
        description="built without a YAML file",
        base_url="https://example.invalid",
        http_call=_fake_http_call,
    )
    result = tool.invoke()
    assert result["url"] == "https://example.invalid"
