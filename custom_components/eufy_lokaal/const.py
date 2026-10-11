"""Constanten van Eufy Lokaal."""
from __future__ import annotations

DOMAIN = "eufy_lokaal"
PLATFORMS = ["sensor", "select", "alarm_control_panel"]

CONF_LAND = "land"
CONF_EMAIL = "email"
CONF_WACHTWOORD = "wachtwoord"
CONF_SESSIE = "sessie"  # sleutels en token, zodat een herstart niet opnieuw inlogt

STANDAARD_LAND = "NL"

# Hoe vaak de lijst met apparaten uit de cloud wordt ververst (stap 1). De
# lokale zoektocht (P2P) draait los daarvan, elke LOKAAL_INTERVAL_S.
CLOUD_INTERVAL_S = 30 * 60
LOKAAL_INTERVAL_S = 60
