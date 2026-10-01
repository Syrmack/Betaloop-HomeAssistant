"""Client HTTP minimal pour l'API REST du serveur Betaloop."""

from __future__ import annotations

from typing import Any

import aiohttp


class BetaloopError(Exception):
    """Erreur générique de communication avec le serveur."""


class BetaloopAuthError(BetaloopError):
    """Clé API refusée par le serveur (401)."""


class BetaloopApi:
    """Accès en lecture aux routes /api/v1/* du serveur Betaloop."""

    def __init__(self, session: aiohttp.ClientSession, url: str, api_key: str) -> None:
        self._session = session
        self._url = url.rstrip("/")
        self._headers = {"X-API-Key": api_key}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            async with self._session.get(
                f"{self._url}{path}",
                params={k: v for k, v in (params or {}).items() if v is not None},
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status == 401:
                    raise BetaloopAuthError("Clé API manquante ou invalide")
                if resp.status >= 400:
                    raise BetaloopError(f"{path} : HTTP {resp.status}")
                return await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise BetaloopError(f"{path} : {err}") from err

    async def _get_all(self, path: str, from_ts: int | None, to_ts: int | None = None) -> list[dict]:
        """Parcourt toutes les pages d'une ressource (réponse {items, total, limit, offset})."""
        items: list[dict] = []
        offset = 0
        while True:
            page = await self._get(
                path, {"from_ts": from_ts, "to_ts": to_ts, "limit": 5000, "offset": offset}
            )
            items.extend(page["items"])
            offset += len(page["items"])
            if not page["items"] or offset >= page["total"]:
                return items

    async def check(self) -> None:
        """Vérifie que le serveur répond et que la clé API est acceptée."""
        await self._get("/api/v1/glucose", {"limit": 1})

    async def latest_glucose(self) -> dict | None:
        page = await self._get("/api/v1/glucose", {"limit": 1})
        return page["items"][0] if page["items"] else None

    async def glucose(self, from_ts: int) -> list[dict]:
        return await self._get_all("/api/v1/glucose", from_ts)

    async def doses(self, from_ts: int) -> list[dict]:
        return await self._get_all("/api/v1/doses", from_ts)

    async def meals(self, from_ts: int) -> list[dict]:
        return await self._get_all("/api/v1/meals", from_ts)

    async def steps(self, from_ts: int) -> list[dict]:
        return await self._get_all("/api/v1/steps", from_ts)

    async def summary(self, days: int) -> dict:
        return await self._get("/api/v1/summary", {"days": days})
