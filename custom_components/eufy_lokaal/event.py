"""Elke gebeurtenis van een camera als event-entiteit (voor automatiseringen)."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import EufyLokaalCoordinator
from .gebeurtenissen import GEBEURTENISSOORTEN
from .sensor import _apparaat_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    c: EufyLokaalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(CameraGebeurtenis(c, sn) for sn in c.apparaten if sn not in c.stations)


class CameraGebeurtenis(EventEntity):
    _attr_has_entity_name = True
    _attr_name = "Gebeurtenis"
    _attr_icon = "mdi:cctv"
    _attr_event_types = GEBEURTENISSOORTEN

    def __init__(self, c: EufyLokaalCoordinator, sn: str) -> None:
        self._c, self._sn = c, sn
        self._attr_unique_id = f"{sn}_gebeurtenis"
        self._attr_device_info = _apparaat_info(c, sn)

    async def async_added_to_hass(self) -> None:
        self._c._bij_gebeurtenis.append(self._bij)
        self.async_on_remove(lambda: self._c._bij_gebeurtenis.remove(self._bij))

    @callback
    def _bij(self, sn: str, g: dict) -> None:
        if sn != self._sn:
            return
        self._trigger_event(g["soort"], {"event_type": g.get("event_type"), "persoon": g.get("persoon")})
        self.async_write_ha_state()
