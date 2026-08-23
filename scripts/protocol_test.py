"""Drive the server over a real stdio MCP session, the way a client would.

This spawns the server as a subprocess, completes the MCP handshake, lists the
tools and calls one. It deliberately runs with blank credentials so it needs no
Trello account: the point is to prove the protocol layer works and that a
missing credential surfaces as a readable message instead of a crash.

Run: .venv/Scripts/python scripts/protocol_test.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent


async def main() -> int:
    env = dict(os.environ)
    env["TRELLO_API_KEY"] = ""
    env["TRELLO_TOKEN"] = ""

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "trellis.server"],
        env=env,
        cwd=str(ROOT),
    )

    failures = 0
    async with Client(stdio_client(params)) as client:
        info = client.server_info
        print(f"connected to: {info.name} v{info.version}")
        print(f"protocol:     {client.protocol_version}")
        assert client.instructions, "server should ship usage instructions"
        print(f"instructions: {len(client.instructions)} chars")

        tools = (await client.list_tools()).tools
        print(f"\ntools: {len(tools)}")
        for tool in tools:
            summary = (tool.description or "").split("\n")[0]
            print(f"  {tool.name:26} {summary[:64]}")

        expected = {
            "list_boards", "get_board_info", "list_board_lists", "list_cards",
            "get_card", "search_trello", "analyze_board_health", "detect_bottlenecks",
            "generate_standup_report", "create_card", "move_card", "update_card",
            "add_comment",
        }
        actual = {t.name for t in tools}
        if actual != expected:
            print(f"\nFAIL missing={expected - actual} unexpected={actual - expected}")
            failures += 1
        else:
            print("\nPASS all 13 tools exposed with schemas")

        for tool in tools:
            if not (tool.description or "").strip():
                print(f"FAIL {tool.name} has no description")
                failures += 1

        # Calling without credentials must produce a readable error, not a traceback.
        print("\ncalling list_boards with blank credentials...")
        result = await client.call_tool("list_boards", {})
        payload = _payload(result)
        message = json.dumps(payload)
        if "TRELLO_API_KEY" in message and "power-ups/admin" in message:
            print(f"PASS actionable error returned: {payload.get('error', message)[:140]}")
        else:
            print(f"FAIL unexpected result: {message[:300]}")
            failures += 1

    print("\nprotocol test:", "PASSED" if not failures else f"{failures} FAILURE(S)")
    return 1 if failures else 0


def _payload(result) -> dict:
    if getattr(result, "structured_content", None):
        return result.structured_content
    for block in result.content or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except ValueError:
                return {"error": text}
    return {}


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
