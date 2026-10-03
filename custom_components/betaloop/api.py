"""Client HTTP minimal pour l'API REST du serveur Betaloop."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp


class BetaloopError(Exception):
    """Erreur générique de communication avec le serveur."""


class BetaloopAuthError(BetaloopError):
    """Clé API refusée par le serveur (401)."""


class BetaloopNotFoundError(BetaloopError):
    """Route absente (404) : serveur dans une version antérieure à son ajout."""


class BetaloopApi:
    """Accès en lecture aux routes /api/v1/* du serveur Betaloop."""

    timeout_s: float = 20
    attempts: int = 3
    retry_delay_s: float = 2

    def __init__(self, session: aiohttp.ClientSession, url: str, api_key: str) -> None:
        self._session = session
        self._url = url.rstrip("/")
        # "Connection: close" : pas de connexion persistante réutilisée d'un cycle à l'autre.
        # Entre deux interrogations (5 min), une connexion gardée ouverte peut être coupée sans
        # prévenir par un NAT, Docker ou un VPN ; la requête suivante attend alors une réponse
        # qui n'arrive jamais, jusqu'au délai d'expiration.
        self._headers = {"X-API-Key": api_key, "Connection": "close"}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET avec nouvelles tentatives sur délai dépassé ou connexion perdue (pas sur une
        réponse HTTP d'erreur, qui ne changerait pas en réessayant)."""
        for attempt in range(1, self.attempts + 1):
            try:
                return await self._get_once(path, params)
            except BetaloopError as err:
                if not isinstance(err.__cause__, (aiohttp.ClientConnectionError, TimeoutError)):
                    raise
                if attempt == self.attempts:
                    raise
                await asyncio.sleep(self.retry_delay_s)
        raise AssertionError("unreachable")

    async def _get_once(self, path: str, params: dict[str, Any] | None) -> Any:
        try:
            async with self._session.get(
                f"{self._url}{path}",
                params={k: v for k, v in (params or {}).items() if v is not None},
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout_s),
            ) as resp:
                if resp.status == 401:
                    raise BetaloopAuthError("Clé API manquante ou invalide")
                if resp.status == 404:
                    raise BetaloopNotFoundError(f"{path} : HTTP 404")
                if resp.status >= 400:
                    raise BetaloopError(f"{path} : HTTP {resp.status}")
                return await resp.json()
        except TimeoutError as err:
            # str(TimeoutError()) est vide : sans ce message le journal n'affichait que le chemin.
            raise BetaloopError(
                f"{path} : pas de réponse du serveur en {self.timeout_s:g} s"
            ) from err
        except aiohttp.ClientError as err:
            raise BetaloopError(f"{path} : {type(err).__name__} {err}") from err

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
        """Vérifie que le serveur répond et que la clé API est acceptée. Sans nouvelle tentative :
        dans le formulaire de configuration, mieux vaut une erreur rapide qu'une longue attente."""
        await self._get_once("/api/v1/glucose", {"limit": 1})

    async def latest_glucose(self) -> dict | None:
        page = await self._get("/api/v1/glucose", {"limit": 1})
        return page["items"][0] if page["items"] else None

    async def glucose(self, from_ts: int) -> list[dict]:
        return await self._get_all("/api/v1/glucose", from_ts)

    async def doses(self, from_ts: int) -> list[dict]:
        return await self._get_all("/api/v1/doses", from_ts)

    async def meals(self, from_ts: int) -> list[dict]:
        return await self._get_all("/api/v1/meals", from_ts)

    async def steps(self, from_ts: int) -> list[dict] | None:
        """None si le serveur est antérieur à la 1.2 (pas de route /api/v1/steps)."""
        try:
            return await self._get_all("/api/v1/steps", from_ts)
        except BetaloopNotFoundError:
            return None

    async def current_sensor(self) -> dict | None:
        """Capteur actif et son expiration ; None si aucun capteur reçu ou serveur antérieur à
        la 1.5 (pas de route /api/v1/sensor)."""
        try:
            return await self._get("/api/v1/sensor")
        except BetaloopNotFoundError:
            return None

    async def settings(self) -> dict | None:
        """Seuils réglés dans l'app (low_mgdl, high_mgdl) ; None si le serveur est antérieur à la
        1.7 (pas de route /api/v1/settings)."""
        try:
            return await self._get("/api/v1/settings")
        except BetaloopNotFoundError:
            return None

    async def server_version(self) -> str | None:
        """Version déployée du serveur ; None s'il est antérieur à la 1.4 (/health sans version)."""
        return (await self._get("/health")).get("version")

    async def summary(self, days: int) -> dict:
        return await self._get("/api/v1/summary", {"days": days})
