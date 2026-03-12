from __future__ import annotations

import sys
from types import SimpleNamespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from claude_code_adapter_app.anthropic_gateway import _anthropic_to_gigachat, _gigachat_to_anthropic


def test_anthropic_to_gigachat_maps_tools_and_tool_results() -> None:
    payload = {
        "model": "gigachat/GigaChat-2",
        "system": [{"type": "text", "text": "System rules"}],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Read the file"}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Read",
                        "input": {"file_path": "/tmp/example.txt"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "file contents",
                        "is_error": False,
                    }
                ],
            },
        ],
        "tools": [
            {
                "name": "Read",
                "description": "Read a file",
                "input_schema": {
                    "type": "object",
                    "properties": {"file_path": {"type": "string"}},
                    "required": ["file_path"],
                },
            }
        ],
    }

    translated = _anthropic_to_gigachat(payload, model="gigachat/GigaChat-2")
    assert translated["model"] == "gigachat/GigaChat-2"
    assert translated["function_call"] == "auto"
    assert translated["functions"][0]["name"] == "Read"
    assert translated["messages"][0]["role"] == "system"
    assert translated["messages"][1]["role"] == "user"
    assert translated["messages"][2]["role"] == "assistant"
    assert translated["messages"][2]["function_call"]["name"] == "Read"
    assert translated["messages"][3]["role"] == "function"
    assert "Read result: file contents" == translated["messages"][3]["content"]


def test_gigachat_to_anthropic_maps_function_call_to_tool_use() -> None:
    completion = SimpleNamespace(
        model="gigachat/GigaChat-2",
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4),
        choices=[
            SimpleNamespace(
                finish_reason="function_call",
                message=SimpleNamespace(
                    content="",
                    function_call=SimpleNamespace(name="Read", arguments={"file_path": "/tmp/example.txt"}),
                ),
            )
        ],
    )

    translated = _gigachat_to_anthropic(completion)
    assert translated["model"] == "gigachat/GigaChat-2"
    assert translated["stop_reason"] == "tool_use"
    assert translated["content"][0]["type"] == "tool_use"
    assert translated["content"][0]["name"] == "Read"
    assert translated["content"][0]["input"]["file_path"] == "/tmp/example.txt"
    assert translated["usage"]["input_tokens"] == 12
    assert translated["usage"]["output_tokens"] == 4
