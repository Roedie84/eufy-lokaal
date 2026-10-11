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
    _herstel_entiteit_ids(hass, entry)
    if not hass.services.has_service(DOMAIN, "momentopname"):
        hass.services.async_register(DOMAIN, "momentopname", _momentopname_dienst(hass))
    return True


def _herstel_entiteit_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Eenmalig: entiteit-ID's opnieuw laten maken zoals Home Assistant dat zelf
    doet (ruimte + apparaat + naam). Nodig als een apparaat van ruimte is
    gewisseld: Home Assistant geeft verwijderde entiteiten hun oude ID terug.
    Heet het apparaat net als zijn ruimte ("Zolder" op de zolder), dan komt de
    naam maar één keer in het ID: sensor.zolder_batterij, niet zolder_zolder."""
    import logging
    import re

    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from homeassistant.util import slugify

    if entry.data.get("entiteit_ids", 0) >= 2:
        return
    reg = er.async_get(hass)
    apparaten, ruimtes = dr.async_get(hass), ar.async_get(hass)
    log = logging.getLogger(__name__)
    gereserveerd: set[str] = set()
    for e in er.async_entries_for_config_entry(reg, entry.entry_id):
        nieuw = e.entity_id
        if entry.data.get("entiteit_ids") != 1 and hasattr(reg, "async_regenerate_entity_id"):
            try:
                nieuw = reg.async_regenerate_entity_id(e, reserved_entity_ids=gereserveerd)
            except Exception:  # noqa: BLE001
                pass
        apparaat = apparaten.async_get(e.device_id) if e.device_id else None
        ruimte = ruimtes.async_get_area(apparaat.area_id) if apparaat and apparaat.area_id else None
        if apparaat and ruimte:
            naam = slugify(apparaat.name_by_user or apparaat.name or "")
            domein, obj = nieuw.split(".", 1)
            if naam and slugify(ruimte.name) == naam and (obj == f"{naam}_{naam}" or obj.startswith(f"{naam}_{naam}_")):
                obj = naam + obj[len(naam) * 2 + 1:]
                basis = re.sub(r"_\d+$", "", obj)
                if basis != obj and reg.async_get(f"{domein}.{basis}") is None and f"{domein}.{basis}" not in gereserveerd:
                    obj = basis
                kandidaat = f"{domein}.{obj}"
                if reg.async_get(kandidaat) is None or kandidaat == e.entity_id:
                    nieuw = kandidaat
        gereserveerd.add(nieuw)
        if nieuw != e.entity_id and reg.async_get(nieuw) is None:
            log.warning("Entiteit-ID %s wordt %s", e.entity_id, nieuw)
            reg.async_update_entity(e.entity_id, new_entity_id=nieuw)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "entiteit_ids": 2})


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
