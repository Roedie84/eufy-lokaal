"""Stap 4: de HomeBase als alarmpaneel, lokaal.

De toestand volgt de actuele alarmstand (1151) die de HomeBase meldt;
inschakelen zet de bewakingsstand (weg, thuis, aangepast 1) en uitschakelen
zet hem op uitgeschakeld."""
from __future__ import annotations

import time

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EufyLokaalCoordinator
from .sensor import _station_info

TOESTAND = {
    0: AlarmControlPanelState.ARMED_AWAY,
    1: AlarmControlPanelState.ARMED_HOME,
    3: AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
    4: AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
    5: AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
    63: AlarmControlPanelState.DISARMED,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    c: EufyLokaalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(HomeBaseAlarm(c, sn) for sn in c.stations)


class HomeBaseAlarm(CoordinatorEntity[EufyLokaalCoordinator], AlarmControlPanelEntity):
    _attr_has_entity_name = True
    _attr_name = "Alarm"
    _attr_code_arm_required = False
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS
    )

    def __init__(self, c: EufyLokaalCoordinator, sn: str) -> None:
        super().__init__(c)
        self._sn = sn
        self._attr_unique_id = f"{sn}_alarm"
        self._attr_device_info = _station_info(c, sn)

    @property
    def available(self) -> bool:
        s = self.coordinator.sessies.get(self._sn)
        return s is not None and s.levend and bool(self.coordinator.alarmstand.get(self._sn))

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        m = self.coordinator.laatste_melding.get(self._sn) or {}
        if m.get("soort") == "vertraagd_alarm" and time.time() - m.get("tijd", 0) < (m.get("vertraging_s") or 0):
            return AlarmControlPanelState.PENDING
        return TOESTAND.get((self.coordinator.alarmstand.get(self._sn) or {}).get("waarde"))

    @property
    def extra_state_attributes(self) -> dict:
        a = self.coordinator.alarmstand.get(self._sn) or {}
        return {"alarmstand": a.get("naam"), "bewakingsstand": self.coordinator.bewakingsstand(self._sn),
                "bron": a.get("bron")}

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self.coordinator.async_zet_bewakingsstand(self._sn, 63)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self.coordinator.async_zet_bewakingsstand(self._sn, 0)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self.coordinator.async_zet_bewakingsstand(self._sn, 1)

    async def async_alarm_arm_custom_bypass(self, code: str | None = None) -> None:
        await self.coordinator.async_zet_bewakingsstand(self._sn, 3)
