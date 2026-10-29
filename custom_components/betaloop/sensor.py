"""Capteurs Betaloop : glycémie courante, tendance, variation, glycémie prévue, statistiques, insuline,
glucides, pas, expiration du capteur CGM."""

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
    DELTA_MAX_GAP,
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
UNRECORDED_ATTRIBUTES = frozenset({"readings", "doses", "meals", "forecast"})


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


def _delta_pair(data: BetaloopData) -> tuple[dict, dict] | None:
    """Dernier relevé (s'il est récent) et le précédent, s'ils sont espacés de moins de DELTA_MAX_GAP."""
    if _fresh_latest(data) is None or len(data.readings) < 2:
        return None
    previous, latest = data.readings[-2], data.readings[-1]
    gap_ms = latest["timestamp"] - previous["timestamp"]
    if gap_ms <= 0 or gap_ms > DELTA_MAX_GAP.total_seconds() * 1000:
        return None
    return previous, latest


def _delta(data: BetaloopData) -> int | None:
    pair = _delta_pair(data)
    return pair[1]["value_mgdl"] - pair[0]["value_mgdl"] if pair else None


def _delta_attrs(data: BetaloopData) -> dict[str, Any]:
    # L'écart varie : un relevé par minute en surveillance continue, un par quart d'heure dans
    # l'historique LibreLinkUp. "+12" ne se lit pas pareil sur 1 ou 15 minutes.
    pair = _delta_pair(data)
    if pair is None:
        return {"minutes": None, "previous_time": None}
    previous, latest = pair
    return {
        "minutes": max(1, round((latest["timestamp"] - previous["timestamp"]) / 60000)),
        "previous_time": _ts(previous["timestamp"]).isoformat(),
    }


def _glucose_attrs(data: BetaloopData) -> dict[str, Any]:
    latest = data.latest
    # Seuils réglés dans l'app (serveur 1.7), sinon ceux par défaut.
    settings = data.settings or {}
    return {
        "trend": _trend(data),
        "reading_time": _ts(latest["timestamp"]).isoformat() if latest else None,
        "target_low": settings.get("low_mgdl", TARGET_LOW_MGDL),
        "target_high": settings.get("high_mgdl", TARGET_HIGH_MGDL),
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


def _range_attrs(data: BetaloopData) -> dict[str, Any]:
    """Seuils avec lesquels le serveur a calculé le temps dans la cible (ceux de l'app depuis la 1.7)."""
    summary = data.summary_24h
    return {
        "target_low": summary.get("target_low_mgdl", TARGET_LOW_MGDL),
        "target_high": summary.get("target_high_mgdl", TARGET_HIGH_MGDL),
    }


def _gmi(data: BetaloopData) -> float | None:
    """GMI du serveur (1.8 ou plus), le même que l'onglet Stats de l'app : relevés ramenés à une
    valeur par quart d'heure, 3,31 + 0,02392 × moyenne. Serveur plus ancien : HbA1c estimée ADAG."""
    consensus = data.summary_hba1c.get("consensus")
    if consensus is not None:
        return consensus.get("gmi_pct")
    return data.summary_hba1c.get("estimated_hba1c_percent")


def _gmi_attrs(data: BetaloopData) -> dict[str, Any]:
    consensus = data.summary_hba1c.get("consensus") or {}
    return {
        "period_days": HBA1C_PERIOD_DAYS,
        "formula": "GMI" if consensus else "ADAG",
        # Représentatif seulement si au moins 70 % de la période est couverte par des relevés.
        "coverage_pct": consensus.get("coverage_pct"),
        "average_mgdl": consensus.get("average_mgdl"),
        "estimated_hba1c_adag": data.summary_hba1c.get("estimated_hba1c_percent"),
    }


def _prediction(data: BetaloopData) -> dict | None:
    """Prévision du serveur (1.14), seulement si elle est disponible (une dose agit ou un repas
    s'absorbe, relevé récent, paramètres du calculateur reçus)."""
    prediction = data.prediction
    return prediction if prediction and prediction.get("available") else None


def _prediction_point(data: BetaloopData, key: str) -> int | None:
    point = (_prediction(data) or {}).get(key)
    return point["mgdl"] if point else None


def _prediction_point_attrs(key: str) -> Callable[[BetaloopData], dict[str, Any]]:
    def attrs(data: BetaloopData) -> dict[str, Any]:
        point = (_prediction(data) or {}).get(key)
        return {"time": _ts(point["timestamp"]).isoformat() if point else None}

    return attrs


def _prediction_attrs(data: BetaloopData) -> dict[str, Any]:
    prediction = data.prediction or {}
    available = _prediction(data) is not None
    return {
        # Pourquoi il n'y a pas de prévision (rien n'agit, pas de relevé récent...) ; None si elle
        # est disponible ou si le serveur est antérieur à la 1.14.
        "reason": prediction.get("reason"),
        "in_30min": prediction.get("in_30min_mgdl"),
        "in_120min": prediction.get("in_120min_mgdl"),
        "until": _ts(prediction["until"]).isoformat() if available else None,
        # [[epoch ms, mg/dL], ...] toutes les 5 min jusqu'à la fin d'action ou d'absorption, pour
        # apexcharts-card.
        "forecast": [[p["timestamp"], p["mgdl"]] for p in prediction.get("points", [])] if available else [],
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
        key="glucose_delta",
        translation_key="glucose_delta",
        # Classe concentration : HA convertit aussi la variation en mmol/L si demandé (simple
        # facteur, sans décalage, donc valable pour une différence).
        device_class=SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION,
        native_unit_of_measurement=MGDL,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:plus-minus-variant",
        value_fn=_delta,
        attrs_fn=_delta_attrs,
    ),
    # Glycémie prévue (serveur 1.14) : inconnue tant qu'aucune dose ni aucun repas aux glucides
    # saisis n'agit. Une projection des paramètres du calculateur, pas une certitude.
    BetaloopSensorDescription(
        key="predicted_glucose",
        translation_key="predicted_glucose",
        device_class=SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION,
        native_unit_of_measurement=MGDL,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:chart-bell-curve-cumulative",
        value_fn=lambda d: (p := _prediction(d)) and p.get("in_60min_mgdl"),
        attrs_fn=_prediction_attrs,
    ),
    BetaloopSensorDescription(
        key="predicted_low",
        translation_key="predicted_low",
        device_class=SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION,
        native_unit_of_measurement=MGDL,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:arrow-down-bold-circle-outline",
        value_fn=lambda d: _prediction_point(d, "lowest"),
        attrs_fn=_prediction_point_attrs("lowest"),
    ),
    BetaloopSensorDescription(
        key="predicted_high",
        translation_key="predicted_high",
        device_class=SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION,
        native_unit_of_measurement=MGDL,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:arrow-up-bold-circle-outline",
        value_fn=lambda d: _prediction_point(d, "highest"),
        attrs_fn=_prediction_point_attrs("highest"),
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
        attrs_fn=_range_attrs,
    ),
    BetaloopSensorDescription(
        key="time_below_range_24h",
        translation_key="time_below_range_24h",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:arrow-collapse-down",
        value_fn=lambda d: d.summary_24h.get("time_below_range_pct"),
        attrs_fn=_range_attrs,
    ),
    BetaloopSensorDescription(
        key="time_above_range_24h",
        translation_key="time_above_range_24h",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:arrow-collapse-up",
        value_fn=lambda d: d.summary_24h.get("time_above_range_pct"),
        attrs_fn=_range_attrs,
    ),
    BetaloopSensorDescription(
        key="estimated_hba1c",
        translation_key="estimated_hba1c",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-percent",
        value_fn=_gmi,
        attrs_fn=_gmi_attrs,
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
