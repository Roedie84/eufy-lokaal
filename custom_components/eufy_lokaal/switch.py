"""v0.11.0: per camera de beeldherkenning (personen, gezichten, hond) aan of uit.

Uit = geen beoordeling en geen extra live beeld, dus ook geen extra batterijverbruik.
De stand overleeft een herstart (RestoreEntity)."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EufyLokaalCoordinator
from .sensor import _apparaat_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    c: EufyLokaalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(HerkenningSchakelaar(c, sn) for sn in c.apparaten if c.kan_herkennen(sn))


class HerkenningSchakelaar(CoordinatorEntity[EufyLokaalCoordinator], SwitchEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "Beeldherkenning"
    _attr_icon = "mdi:account-search"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, c: EufyLokaalCoordinator, sn: str) -> None:
        super().__init__(c)
        self._sn = sn
        self._attr_unique_id = f"{sn}_beeldherkenning"
        self._attr_device_info = _apparaat_info(c, sn)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        vorig = await self.async_get_last_state()
        if vorig is not None and vorig.state == "off":
            self.coordinator.zet_herkenning(self._sn, False)

    @property
    def is_on(self) -> bool:
        return self.coordinator.herkent(self._sn)

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.zet_herkenning(self._sn, True)

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.zet_herkenning(self._sn, False)
