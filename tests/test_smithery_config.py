"""Checks for the hosted (Smithery) config path. No network, no credentials.

The important one is test_concurrent_requests_do_not_leak_credentials: on a
hosted server one process serves many users, and the obvious implementation
(a module-level global) silently sends one user's token with another user's
request. Run: .venv/Scripts/python tests/test_smithery_config.py
"""

from __future__ import annotations

import asyncio
import base64
import json

from trellis import config
from trellis.smithery_app import MCPPathRedirect, SmitheryConfigMiddleware, _parse_config


def encode(payload: dict) -> bytes:
    blob = base64.b64encode(json.dumps(payload).encode()).decode()
    return f"config={blob}".encode()


def test_parse_config_decodes_base64_json():
    parsed = _parse_config(encode({"trelloApiKey": "k1", "trelloToken": "t1"}))
    assert parsed == {"trelloApiKey": "k1", "trelloToken": "t1"}


def test_parse_config_survives_junk():
    for bad in [b"", b"config=", b"config=not-base64!!", b"other=1", b"config=" + base64.b64encode(b"[1,2]")]:
        assert _parse_config(bad) == {}, f"should ignore {bad!r}"


def test_middleware_binds_credentials_for_the_request():
    seen = {}

    async def app(scope, receive, send):
        seen["creds"] = config.load_credentials()

    scope = {"type": "http", "path": "/mcp/", "query_string": encode(
        {"trelloApiKey": "key-abc", "trelloToken": "tok-xyz"}
    )}
    asyncio.run(SmitheryConfigMiddleware(app)(scope, None, None))

    assert seen["creds"].api_key == "key-abc"
    assert seen["creds"].token == "tok-xyz"
    assert config._session_credentials.get() is None, "must not leak past the request"


def test_missing_config_still_reaches_the_app():
    """Tool listing has to work before anyone supplies a token."""
    called = []

    async def app(scope, receive, send):
        called.append(True)

    scope = {"type": "http", "path": "/mcp/", "query_string": b""}
    asyncio.run(SmitheryConfigMiddleware(app)(scope, None, None))
    assert called == [True]


def test_concurrent_requests_do_not_leak_credentials():
    """Two users, one process. Neither may see the other's token."""
    observed: dict[str, str] = {}

    async def app(scope, receive, send):
        user = scope["user"]
        # Yield control mid-request so the two requests genuinely interleave.
        await asyncio.sleep(0.01)
        observed[user] = config.load_credentials().token

    middleware = SmitheryConfigMiddleware(app)

    async def run(user: str, token: str):
        scope = {
            "type": "http",
            "path": "/mcp/",
            "user": user,
            "query_string": encode({"trelloApiKey": f"key-{user}", "trelloToken": token}),
        }
        await middleware(scope, None, None)

    async def both():
        await asyncio.gather(run("alice", "tok-alice"), run("bob", "tok-bob"))

    asyncio.run(both())
    assert observed == {"alice": "tok-alice", "bob": "tok-bob"}, (
        f"credential leak between concurrent requests: {observed}"
    )


def test_path_redirect_normalises_mcp():
    """/mcp/ must collapse to /mcp, the route this SDK mounts.

    The reverse rewrite (what the Smithery cookbook does, for FastMCP 1.x)
    fights Starlette redirect_slashes here and loops forever.
    """
    seen = {}

    async def app(scope, receive, send):
        seen["path"] = scope["path"]

    asyncio.run(MCPPathRedirect(app)({"type": "http", "path": "/mcp/"}, None, None))
    assert seen["path"] == "/mcp"

    asyncio.run(MCPPathRedirect(app)({"type": "http", "path": "/mcp"}, None, None))
    assert seen["path"] == "/mcp"

    asyncio.run(MCPPathRedirect(app)({"type": "http", "path": "/other"}, None, None))
    assert seen["path"] == "/other"


def test_non_mcp_paths_are_passed_through_untouched():
    called = []

    async def app(scope, receive, send):
        called.append(scope["path"])
        assert config._session_credentials.get() is None

    scope = {"type": "http", "path": "/health", "query_string": encode(
        {"trelloApiKey": "k", "trelloToken": "t"}
    )}
    asyncio.run(SmitheryConfigMiddleware(app)(scope, None, None))
    assert called == ["/health"]


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
                passed += 1
            except AssertionError as exc:
                print(f"  FAIL  {name}: {exc}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
