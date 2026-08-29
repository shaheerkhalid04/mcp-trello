"""HTTP entrypoint for hosted deployment (Smithery).

Smithery runs the container, sets PORT, and calls the MCP streamable HTTP
endpoint at /mcp. Per-user configuration arrives as a base64-encoded JSON blob
in the `config` query parameter on every request, which is where the Trello
credentials come from when running hosted rather than from the environment.

Run locally:  python -m trellis.smithery_app
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
from urllib.parse import parse_qs, unquote

from starlette.middleware.cors import CORSMiddleware

from .config import reset_session_credentials, set_session_credentials
from .server import server

logger = logging.getLogger(__name__)

MCP_PATHS = ("/mcp", "/mcp/")


class SmitheryConfigMiddleware:
    """Bind the caller's Trello credentials to the request being served.

    The credentials go into a ContextVar rather than a module global, so
    concurrent requests from different users cannot overwrite each other.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in MCP_PATHS:
            await self.app(scope, receive, send)
            return

        config = _parse_config(scope.get("query_string", b""))
        api_key = str(config.get("trelloApiKey") or "").strip()
        token = str(config.get("trelloToken") or "").strip()

        if not (api_key and token):
            # Not an error: tool listing must work unconfigured, so Smithery can
            # show the tools before anyone supplies a token. The tools themselves
            # return an actionable message when credentials are missing.
            await self.app(scope, receive, send)
            return

        reset = set_session_credentials(api_key, token)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_session_credentials(reset)


class MCPPathRedirect:
    """Normalise /mcp/ to /mcp, which is where this SDK mounts the endpoint.

    Worth stating because the Smithery Python cookbook does the opposite: it
    rewrites /mcp to /mcp/, which was right for FastMCP 1.x. Under MCP SDK 2.x
    the route is /mcp and Starlette's redirect_slashes bounces /mcp/ back to
    /mcp, so that rewrite produces an infinite redirect loop. Normalising in
    this direction keeps both spellings working.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http" and scope.get("path") == "/mcp/":
            scope = {**scope, "path": "/mcp", "raw_path": b"/mcp"}
        await self.app(scope, receive, send)


def _parse_config(query_string: bytes) -> dict:
    """Decode Smithery's base64 JSON `config` query parameter.

    Never raises and never logs the decoded values, since they are credentials.
    A malformed config is treated as no config.
    """
    try:
        params = parse_qs(query_string.decode("utf-8", errors="replace"))
    except Exception:
        return {}

    raw = (params.get("config") or [None])[0]
    if not raw:
        return {}

    try:
        decoded = base64.b64decode(unquote(raw), validate=False)
        parsed = json.loads(decoded)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        logger.warning("Ignoring unreadable config parameter: %s", type(exc).__name__)
        return {}

    return parsed if isinstance(parsed, dict) else {}


def build_app():
    """Assemble the ASGI app Smithery serves."""
    app = server.streamable_http_app()

    app = CORSMiddleware(
        app,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id", "mcp-protocol-version"],
        max_age=86400,
    )
    app = SmitheryConfigMiddleware(app)
    return MCPPathRedirect(app)


app = build_app()


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    logging.basicConfig(level=logging.INFO)
    logger.info("Trellis MCP listening on 0.0.0.0:%s at /mcp", port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
