"""Thin async wrapper over the Trello REST API.

Everything the tools need goes through `TrelloClient.request`, so auth,
error translation and timeouts are handled in exactly one place.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import Credentials, load_credentials

API_BASE = "https://api.trello.com/1"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)

# Field sets kept narrow on purpose: Trello returns very large card objects by
# default, and an MCP tool result is spent straight out of the model's context.
CARD_FIELDS = (
    "name,desc,url,shortUrl,idList,idBoard,idMembers,labels,due,dueComplete,"
    "closed,dateLastActivity,badges,pos"
)
BOARD_FIELDS = "name,desc,url,shortUrl,closed,dateLastActivity,prefs,idOrganization"
LIST_FIELDS = "name,closed,pos,idBoard"


class TrelloError(RuntimeError):
    """A Trello API call failed in a way the caller should see verbatim."""


class TrelloClient:
    def __init__(self, credentials: Credentials | None = None) -> None:
        self._credentials = credentials or load_credentials()
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=API_BASE, timeout=TIMEOUT)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Call the Trello API and return decoded JSON.

        Trello takes credentials as query parameters on every request, including
        writes, so they are merged in here rather than set as headers.
        """
        query: dict[str, Any] = {
            "key": self._credentials.api_key,
            "token": self._credentials.token,
        }
        for key, value in (params or {}).items():
            if value is not None:
                query[key] = value

        client = await self._http()
        try:
            response = await client.request(method, path, params=query)
        except httpx.RequestError as exc:
            raise TrelloError(f"Could not reach the Trello API: {exc}") from exc

        if response.status_code == 401:
            raise TrelloError(
                "Trello rejected the credentials (401). The token may be expired, "
                "revoked, or scoped without access to this resource."
            )
        if response.status_code == 404:
            raise TrelloError(
                f"Trello returned 404 for {path}. Check the id, and note that a board "
                "or card you cannot see returns 404 rather than 403."
            )
        if response.status_code == 429:
            raise TrelloError(
                "Trello rate limit hit (429). Wait a few seconds and retry; the limit "
                "is 300 requests per 10 seconds per API key."
            )
        if response.status_code >= 400:
            raise TrelloError(
                f"Trello API error {response.status_code} on {path}: {response.text[:400]}"
            )

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    # --- reads -------------------------------------------------------------

    async def my_boards(self, include_closed: bool = False) -> list[dict[str, Any]]:
        return await self.request(
            "GET",
            "/members/me/boards",
            {"fields": BOARD_FIELDS, "filter": "all" if include_closed else "open"},
        )

    async def board(self, board_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/boards/{board_id}", {"fields": BOARD_FIELDS})

    async def board_lists(self, board_id: str) -> list[dict[str, Any]]:
        return await self.request(
            "GET", f"/boards/{board_id}/lists", {"fields": LIST_FIELDS, "filter": "open"}
        )

    async def board_cards(self, board_id: str) -> list[dict[str, Any]]:
        return await self.request(
            "GET", f"/boards/{board_id}/cards", {"fields": CARD_FIELDS, "filter": "open"}
        )

    async def board_members(self, board_id: str) -> list[dict[str, Any]]:
        return await self.request(
            "GET", f"/boards/{board_id}/members", {"fields": "fullName,username"}
        )

    async def board_actions(
        self, board_id: str, limit: int = 50, since: str | None = None
    ) -> list[dict[str, Any]]:
        return await self.request(
            "GET",
            f"/boards/{board_id}/actions",
            {
                "filter": "createCard,updateCard,commentCard,deleteCard",
                "limit": min(max(limit, 1), 1000),
                "since": since,
            },
        )

    async def list_cards(self, list_id: str) -> list[dict[str, Any]]:
        return await self.request("GET", f"/lists/{list_id}/cards", {"fields": CARD_FIELDS})

    async def card(self, card_id: str) -> dict[str, Any]:
        return await self.request(
            "GET", f"/cards/{card_id}", {"fields": CARD_FIELDS, "checklists": "all"}
        )

    async def card_comments(self, card_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return await self.request(
            "GET",
            f"/cards/{card_id}/actions",
            {"filter": "commentCard", "limit": min(max(limit, 1), 100)},
        )

    async def search(self, query: str, limit: int = 20) -> dict[str, Any]:
        return await self.request(
            "GET",
            "/search",
            {
                "query": query,
                "modelTypes": "cards,boards",
                "card_fields": CARD_FIELDS,
                "board_fields": BOARD_FIELDS,
                "cards_limit": min(max(limit, 1), 100),
                "boards_limit": min(max(limit, 1), 100),
                "partial": "true",
            },
        )

    # --- writes ------------------------------------------------------------

    async def create_card(
        self,
        list_id: str,
        name: str,
        desc: str | None = None,
        due: str | None = None,
        position: str = "bottom",
    ) -> dict[str, Any]:
        return await self.request(
            "POST",
            "/cards",
            {"idList": list_id, "name": name, "desc": desc, "due": due, "pos": position},
        )

    async def update_card(self, card_id: str, **fields: Any) -> dict[str, Any]:
        return await self.request("PUT", f"/cards/{card_id}", fields)

    async def add_comment(self, card_id: str, text: str) -> dict[str, Any]:
        return await self.request(
            "POST", f"/cards/{card_id}/actions/comments", {"text": text}
        )
