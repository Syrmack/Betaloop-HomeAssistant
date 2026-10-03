"""Coordinateur : interroge le serveur Betaloop à intervalle régulier."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import BetaloopApi, BetaloopAuthError, BetaloopError
from .const import (
    CONF_HISTORY_HOURS,
    CONF_SCAN_INTERVAL,
    DEFAULT_HISTORY_HOURS,
    DEFAULT_SCAN_INTERVAL_MIN,
    DOMAIN,
    HBA1C_PERIOD_DAYS,
    SLOW_REFRESH_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type BetaloopConfigEntry = ConfigEntry[BetaloopCoordinator]


@dataclass
class BetaloopData:
    """Instantané des données du serveur. Les listes sont triées du plus ancien au plus récent."""

    latest: dict | None
    readings: list[dict] = field(default_factory=list)
    doses: list[dict] = field(default_factory=list)
    meals: list[dict] = field(default_factory=list)
    steps_today: int | None = None
    summary_24h: dict = field(default_factory=dict)
    summary_hba1c: dict = field(default_factory=dict)
    server_version: str | None = None
    # Capteur actif (serial_number, activated_at, expires_at... en epoch ms) ; None si inconnu.
    sensor: dict | None = None


@dataclass
class _SlowData:
    """Données relues seulement toutes les SLOW_REFRESH_INTERVAL (voir BetaloopCoordinator)."""

    summary_hba1c: dict
    server_version: str | None
    sensor: dict | None


async def _cached(value: _SlowData) -> _SlowData:
    return value


class BetaloopCoordinator(DataUpdateCoordinator[BetaloopData]):
    """Récupère en un cycle tout ce dont les capteurs et le graphique ont besoin."""

    config_entry: BetaloopConfigEntry

    def __init__(self, hass: HomeAssistant, entry: BetaloopConfigEntry, api: BetaloopApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MIN)
            ),
        )
        self.api = api
        self.history_hours: int = entry.options.get(CONF_HISTORY_HOURS, DEFAULT_HISTORY_HOURS)
        self._server_version: str | None = None
        # Données qui ne bougent presque pas d'un cycle à l'autre (HbA1c sur 14 jours, version du
        # serveur, capteur actif) : relues seulement toutes les SLOW_REFRESH_INTERVAL.
        self._slow: _SlowData | None = None
        self._slow_fetched_at: datetime | None = None

    async def _async_update_data(self) -> BetaloopData:
        now = dt_util.now()
        from_ts = int((now - timedelta(hours=self.history_hours)).timestamp() * 1000)
        today_ts = int(dt_util.start_of_local_day(now).timestamp() * 1000)
        refresh_slow = (
            self._slow is None
            or self._slow_fetched_at is None
            or now - self._slow_fetched_at >= SLOW_REFRESH_INTERVAL
        )

        try:
            readings, doses, meals, steps, summary_24h, slow = await asyncio.gather(
                self.api.glucose(from_ts),
                self.api.doses(from_ts),
                self.api.meals(from_ts),
                self.api.steps(today_ts),
                self.api.summary(1),
                self._fetch_slow() if refresh_slow else _cached(self._slow),
            )
            # Le relevé le plus récent est en tête de la série (ordre décroissant) ; une requête à
            # part n'est utile que si la fenêtre d'historique n'en contient aucun.
            latest = readings[0] if readings else await self.api.latest_glucose()
        except BetaloopAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except BetaloopError as err:
            raise UpdateFailed(str(err)) from err

        if refresh_slow:
            self._slow, self._slow_fetched_at = slow, now
        self._update_device_sw_version(slow.server_version)

        # L'API renvoie du plus récent au plus ancien ; un graphique veut l'ordre chronologique.
        return BetaloopData(
            latest=latest,
            readings=readings[::-1],
            doses=doses[::-1],
            meals=meals[::-1],
            # None : serveur sans route /steps (< 1.2) ou aucune tranche aujourd'hui.
            steps_today=sum(s["count"] for s in steps) if steps else None,
            summary_24h=summary_24h,
            summary_hba1c=slow.summary_hba1c,
            server_version=slow.server_version,
            sensor=slow.sensor,
        )

    async def _fetch_slow(self) -> _SlowData:
        summary_hba1c, server_version, sensor = await asyncio.gather(
            self.api.summary(HBA1C_PERIOD_DAYS),
            self.api.server_version(),
            self.api.current_sensor(),
        )
        return _SlowData(summary_hba1c=summary_hba1c, server_version=server_version, sensor=sensor)

    def _update_device_sw_version(self, version: str | None) -> None:
        """Reporte une nouvelle version du serveur (mise à jour déployée) sur l'appareil : la
        DeviceInfo des capteurs n'est lue qu'à leur création. Au premier cycle l'appareil n'existe
        pas encore ; il sera créé avec la bonne version (voir BetaloopSensor)."""
        if version == self._server_version:
            return
        self._server_version = version
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, self.config_entry.entry_id)})
        if device is not None:
            registry.async_update_device(device.id, sw_version=version)
