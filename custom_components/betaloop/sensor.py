"""Capteurs Betaloop : glycémie courante, tendance, statistiques, insuline, glucides, pas,
expiration du capteur CGM."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONF_URL,
    PERCENTAGE,
    EntityCategory,
    UnitOfBloodGlucoseConcentration,
    UnitOfMass,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    HBA1C_PERIOD_DAYS,
    STALE_AFTER,
    TARGET_HIGH_MGDL,
    TARGET_LOW_MGDL,
    TREND_ARROWS,
    TREND_ICONS,
)
from .coordinator import BetaloopConfigEntry, BetaloopCoordinator, BetaloopData

MGDL = UnitOfBloodGlucoseConcentration.MILLIGRAMS_PER_DECILITER

# Séries destinées au graphique : utiles en direct, mais trop volumineuses pour être
# stockées à chaque changement d'état dans la base du recorder.
UNRECORDED_ATTRIBUTES = frozenset({"readings", "doses", "meals"})


def _ts(ms: int | None) -> datetime | None:
    return dt_util.utc_from_timestamp(ms / 1000) if ms is not None else None


def _fresh_latest(data: BetaloopData) -> dict | None:
    """Dernier relevé, sauf s'il est trop ancien pour être présenté comme la valeur actuelle."""
    latest = data.latest
    if latest is None or dt_util.utcnow() - _ts(latest["timestamp"]) > STALE_AFTER:
        return None
    return latest


def _trend(data: BetaloopData) -> str | None:
    latest = _fresh_latest(data)
    return TREND_ARROWS.get(latest["trend_arrow"]) if latest else None


def _glucose_attrs(data: BetaloopData) -> dict[str, Any]:
    latest = data.latest
    return {
        "trend": _trend(data),
        "reading_time": _ts(latest["timestamp"]).isoformat() if latest else None,
        "target_low": TARGET_LOW_MGDL,
        "target_high": TARGET_HIGH_MGDL,
        # [[epoch ms, mg/dL], ...] du plus ancien au plus récent, pour apexcharts-card.
        "readings": [[r["timestamp"], r["value_mgdl"]] for r in data.readings],
    }


def _doses_attrs(data: BetaloopData) -> dict[str, Any]:
    last = data.doses[-1] if data.doses else None
    return {
        "last_dose_units": last["units"] if last else None,
        "last_dose_type": last["insulin_type_name"] if last else None,
        "last_dose_time": _ts(last["timestamp"]).isoformat() if last else None,
        "doses": [
            {"t": d["timestamp"], "units": d["units"], "type": d["insulin_type_name"]}
            for d in data.doses
        ],
    }


def _meals_attrs(data: BetaloopData) -> dict[str, Any]:
    # .get() : les serveurs antérieurs à la 1.1 n'ont ni glucides ni nature/nom de repas.
    last = data.meals[-1] if data.meals else None
    return {
        "last_meal_time": _ts(last["timestamp"]).isoformat() if last else None,
        "last_meal_kind": last.get("kind") if last else None,
        "last_meal_carbs": last.get("carbs_grams") if last else None,
        "meals": [
            {"t": m["timestamp"], "carbs": m.get("carbs_grams"), "kind": m.get("kind"), "name": m.get("name")}
            for m in data.meals
        ],
    }


def _sensor_expiry(data: BetaloopData) -> datetime | None:
    return _ts(data.sensor["expires_at"]) if data.sensor else None


def _sensor_days_left(data: BetaloopData) -> float | None:
    """Jours restants avant expiration, 0 une fois le capteur expiré."""
    expiry = _sensor_expiry(data)
    if expiry is None:
        return None
    return round(max(0.0, (expiry - dt_util.utcnow()).total_seconds() / 86400), 2)


def _sensor_attrs(data: BetaloopData) -> dict[str, Any]:
    sensor = data.sensor or {}
    return {
        "serial_number": sensor.get("serial_number"),
        "activated_at": _ts(sensor["activated_at"]).isoformat() if sensor else None,
        "lifetime_days": sensor.get("lifetime_days"),
    }


def _total_carbs(data: BetaloopData) -> float:
    return round(sum(m.get("carbs_grams") or 0 for m in data.meals), 1)


@dataclass(frozen=True, kw_only=True)
class BetaloopSensorDescription(SensorEntityDescription):
    value_fn: Callable[[BetaloopData], Any]
    attrs_fn: Callable[[BetaloopData], dict[str, Any]] | None = None
    icon_fn: Callable[[BetaloopData], str | None] | None = None


SENSORS: tuple[BetaloopSensorDescription, ...] = (
    BetaloopSensorDescription(
        key="glucose",
        translation_key="glucose",
        device_class=SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION,
        native_unit_of_measurement=MGDL,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: (latest := _fresh_latest(d)) and latest["value_mgdl"],
        attrs_fn=_glucose_attrs,
    ),
    BetaloopSensorDescription(
        key="trend",
        translation_key="trend",
        device_class=SensorDeviceClass.ENUM,
        options=list(TREND_ARROWS.values()),
        value_fn=_trend,
        icon_fn=lambda d: TREND_ICONS.get(_trend(d) or "", "mdi:help"),
    ),
    BetaloopSensorDescription(
        key="last_reading",
        translation_key="last_reading",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _ts(d.latest["timestamp"]) if d.latest else None,
    ),
    BetaloopSensorDescription(
        key="average_24h",
        translation_key="average_24h",
        device_class=SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION,
        native_unit_of_measurement=MGDL,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.summary_24h.get("average_mgdl"),
    ),
    BetaloopSensorDescription(
        key="time_in_range_24h",
        translation_key="time_in_range_24h",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:bullseye-arrow",
        value_fn=lambda d: d.summary_24h.get("time_in_range_pct"),
    ),
    BetaloopSensorDescription(
        key="time_below_range_24h",
        translation_key="time_below_range_24h",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:arrow-collapse-down",
        value_fn=lambda d: d.summary_24h.get("time_below_range_pct"),
    ),
    BetaloopSensorDescription(
        key="time_above_range_24h",
        translation_key="time_above_range_24h",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:arrow-collapse-up",
        value_fn=lambda d: d.summary_24h.get("time_above_range_pct"),
    ),
    BetaloopSensorDescription(
        key="estimated_hba1c",
        translation_key="estimated_hba1c",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-percent",
        value_fn=lambda d: d.summary_hba1c.get("estimated_hba1c_percent"),
        attrs_fn=lambda d: {"period_days": HBA1C_PERIOD_DAYS},
    ),
    BetaloopSensorDescription(
        key="insulin",
        translation_key="insulin",
        native_unit_of_measurement="U",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:needle",
        value_fn=lambda d: round(sum(x["units"] for x in d.doses), 1),
        attrs_fn=_doses_attrs,
    ),
    BetaloopSensorDescription(
        key="carbs",
        translation_key="carbs",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.GRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:food-apple",
        value_fn=_total_carbs,
        attrs_fn=_meals_attrs,
    ),
    BetaloopSensorDescription(
        key="steps_today",
        translation_key="steps_today",
        native_unit_of_measurement="steps",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:shoe-print",
        value_fn=lambda d: d.steps_today,
    ),
    BetaloopSensorDescription(
        key="sensor_expiry",
        translation_key="sensor_expiry",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:calendar-clock",
        # None (inconnu) : aucun capteur reçu de l'app, ou serveur antérieur à la 1.5.
        value_fn=_sensor_expiry,
        attrs_fn=_sensor_attrs,
    ),
    BetaloopSensorDescription(
        key="sensor_days_left",
        translation_key="sensor_days_left",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer-sand",
        value_fn=_sensor_days_left,
    ),
    BetaloopSensorDescription(
        key="server_version",
        translation_key="server_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:server",
        # None (inconnu) : serveur antérieur à la 1.4, qui n'expose pas sa version.
        value_fn=lambda d: d.server_version,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: BetaloopConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(BetaloopSensor(coordinator, description) for description in SENSORS)


class BetaloopSensor(CoordinatorEntity[BetaloopCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _unrecorded_attributes = UNRECORDED_ATTRIBUTES
    entity_description: BetaloopSensorDescription

    def __init__(self, coordinator: BetaloopCoordinator, description: BetaloopSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        # entity_id stable quelle que soit la langue de HA, pour que la carte fournie dans
        # dashboard/ fonctionne telle quelle (le nom affiché reste traduit).
        self.entity_id = f"sensor.{DOMAIN}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Betaloop",
            manufacturer="Betaloop",
            model="Betaloop Server",
            sw_version=coordinator.data.server_version,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=entry.data[CONF_URL] + "/docs",
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)

    @property
    def icon(self) -> str | None:
        if self.entity_description.icon_fn is not None:
            return self.entity_description.icon_fn(self.coordinator.data)
        return super().icon
