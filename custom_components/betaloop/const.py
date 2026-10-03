"""Constantes de l'intégration Betaloop."""

from datetime import timedelta

DOMAIN = "betaloop"

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL_MIN = 5
MIN_SCAN_INTERVAL_MIN = 1
MAX_SCAN_INTERVAL_MIN = 60

CONF_HISTORY_HOURS = "history_hours"
DEFAULT_HISTORY_HOURS = 24
MAX_HISTORY_HOURS = 72

# Période de calcul de l'HbA1c estimée : une seule journée n'a pas de sens pour cet indicateur.
HBA1C_PERIOD_DAYS = 14

# Rafraîchissement de ce qui bouge peu (HbA1c sur 14 jours, version du serveur, capteur actif) :
# inutile de le relire à chaque interrogation.
SLOW_REFRESH_INTERVAL = timedelta(hours=1)

# Seuils de la plage cible, identiques à ceux du serveur (compute_summary).
TARGET_LOW_MGDL = 70
TARGET_HIGH_MGDL = 180

# Une valeur plus ancienne que ça est considérée périmée (capteur déconnecté, téléphone éteint...).
STALE_AFTER = timedelta(minutes=45)

# Codes TrendArrow de LibreLinkUp (voir GlucosePoller.kt côté app).
TREND_ARROWS = {
    1: "falling_fast",
    2: "falling",
    3: "stable",
    4: "rising",
    5: "rising_fast",
}
TREND_ICONS = {
    "falling_fast": "mdi:arrow-down-bold",
    "falling": "mdi:arrow-bottom-right",
    "stable": "mdi:arrow-right",
    "rising": "mdi:arrow-top-right",
    "rising_fast": "mdi:arrow-up-bold",
}
