"""Eufy Lokaal - Eufy-beveiliging rechtstreeks in Home Assistant.

Eigen implementatie, zonder integratie van derden. Opgebouwd in stappen;
deze versie is stap 1: de apparaten en hun P2P-gegevens uit de cloud, en
nagaan of de basisstations lokaal te vinden zijn. Alleen lezen.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import EufyLokaalCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not hass.data.get(f"{DOMAIN}_view"):
        from .http import LiveView

        hass.http.register_view(LiveView(hass))
        hass.data[f"{DOMAIN}_view"] = True
    coordinator = EufyLokaalCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.start_lokaal()
    if not hass.services.has_service(DOMAIN, "momentopname"):
        hass.services.async_register(DOMAIN, "momentopname", _momentopname_dienst(hass))
    return True


def _momentopname_dienst(hass: HomeAssistant):
    from homeassistant.core import ServiceCall
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import entity_registry as er

    async def dienst(call: ServiceCall) -> None:
        reg = er.async_get(hass)
        ids = call.data.get("entity_id") or []
        if isinstance(ids, str):
            ids = [ids]
        for entity_id in ids:
            e = reg.async_get(entity_id)
            sn = (e.unique_id.rsplit("_", 1)[0] if e and e.platform == DOMAIN else None)
            for c in (hass.data.get(DOMAIN) or {}).values():
                if sn in getattr(c, "apparaten", {}):
                    if not await c.async_momentopname(sn):
                        raise HomeAssistantError(f"Geen live beeld van {entity_id}: "
                                                 f"{(c.momentopnamen.get(sn) or {}).get('fout')}")
                    break
            else:
                raise HomeAssistantError(f"{entity_id} is geen Eufy Lokaal-camera")
    return dienst


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        c = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if c is not None:
            c.stop()
    return ok
