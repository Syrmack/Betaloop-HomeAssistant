"""Coordinateur : interroge le serveur Betaloop à intervalle régulier."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
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

    async def _async_update_data(self) -> BetaloopData:
        now = dt_util.now()
        from_ts = int((now - timedelta(hours=self.history_hours)).timestamp() * 1000)
        today_ts = int(dt_util.start_of_local_day(now).timestamp() * 1000)

        try:
            latest, readings, doses, meals, steps, summary_24h, summary_hba1c = await asyncio.gather(
                self.api.latest_glucose(),
                self.api.glucose(from_ts),
                self.api.doses(from_ts),
                self.api.meals(from_ts),
                self.api.steps(today_ts),
                self.api.summary(1),
                self.api.summary(HBA1C_PERIOD_DAYS),
            )
        except BetaloopAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except BetaloopError as err:
            raise UpdateFailed(str(err)) from err

        # L'API renvoie du plus récent au plus ancien ; un graphique veut l'ordre chronologique.
        return BetaloopData(
            latest=latest,
            readings=readings[::-1],
            doses=doses[::-1],
            meals=meals[::-1],
            steps_today=sum(s["count"] for s in steps) if steps else None,
            summary_24h=summary_24h,
            summary_hba1c=summary_hba1c,
        )
