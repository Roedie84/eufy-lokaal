"""Diagnose: wat de integratie weet, zonder wachtwoord, token of sleutels."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_SESSIE, CONF_WACHTWOORD, DOMAIN

GEHEIM = {
    CONF_WACHTWOORD, CONF_SESSIE, "email", "p2p_license", "push_license", "ndt_license",
    "wipn_enc_dec_key", "wipn_ndt_aes128key", "wakeup_key", "p2p_conn", "app_conn",
    "setup_code", "setup_id", "wifi_mac", "bt_mac", "sub1g_mac", "member", "cover_path",
    "token", "auth_token", "prive_sleutel",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    c = hass.data[DOMAIN][entry.entry_id]
    return async_redact_data(
        {
            "instellingen": dict(entry.data),
            "samenvatting": c.samenvatting(),
            "stations": c.stations,
            "apparaten": c.apparaten,
        },
        GEHEIM,
    )
