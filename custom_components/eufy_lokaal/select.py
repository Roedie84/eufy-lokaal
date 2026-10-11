"""Stap 4: de bewakingsstand van de HomeBase kiezen, lokaal."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .berichten import ALARMSTANDEN
from .const import DOMAIN
from .coordinator import EufyLokaalCoordinator
from .sensor import _station_info

# Wat de app aanbiedt (6 = "uit" bestaat alleen met een keypad).
KEUZES = {w: n for w, n in ALARMSTANDEN.items() if w in (0, 1, 2, 3, 4, 5, 47, 63)}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    c: EufyLokaalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(BewakingsstandSelect(c, sn) for sn in c.stations)


class BewakingsstandSelect(CoordinatorEntity[EufyLokaalCoordinator], SelectEntity):
    _attr_has_entity_name = True
    _attr_name = "Bewakingsstand"
    _attr_icon = "mdi:shield-lock-outline"
    _attr_options = list(KEUZES.values())

    def __init__(self, c: EufyLokaalCoordinator, sn: str) -> None:
        super().__init__(c)
        self._sn = sn
        self._attr_unique_id = f"{sn}_bewakingsstand"
        self._attr_device_info = _station_info(c, sn)

    @property
    def available(self) -> bool:
        s = self.coordinator.sessies.get(self._sn)
        return s is not None and s.levend and self.coordinator.bewakingsstand_waarde(self._sn) is not None

    @property
    def current_option(self) -> str | None:
        return KEUZES.get(self.coordinator.bewakingsstand_waarde(self._sn))

    async def async_select_option(self, option: str) -> None:
        stand = next(w for w, n in KEUZES.items() if n == option)
        await self.coordinator.async_zet_bewakingsstand(self._sn, stand)
