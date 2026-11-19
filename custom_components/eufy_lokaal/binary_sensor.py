"""Detecties per camera (beweging, persoon, aangebeld, ...), lokaal van de HomeBase."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EufyLokaalCoordinator
from .gebeurtenissen import DETECTIES, is_deurbel
from .sensor import _apparaat_info

NAMEN = {"beweging": "Beweging", "persoon": "Persoon", "aangebeld": "Aangebeld", "huisdier": "Huisdier",
         "voertuig": "Voertuig", "geluid": "Geluid", "pakket": "Pakket"}
KLASSEN = {"beweging": BinarySensorDeviceClass.MOTION, "persoon": BinarySensorDeviceClass.OCCUPANCY,
           "geluid": BinarySensorDeviceClass.SOUND}
ICONEN = {"aangebeld": "mdi:doorbell-video", "huisdier": "mdi:paw", "voertuig": "mdi:car", "pakket": "mdi:package-variant"}


_is_deurbel = is_deurbel


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    c: EufyLokaalCoordinator = hass.data[DOMAIN][entry.entry_id]
    uit = []
    for sn, a in c.apparaten.items():
        if sn in c.stations:
            continue
        for naam in DETECTIES:
            if naam == "aangebeld" and not _is_deurbel(a):
                continue
            uit.append(Detectie(c, sn, naam))
    async_add_entities(uit)


class Detectie(CoordinatorEntity[EufyLokaalCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, c: EufyLokaalCoordinator, sn: str, naam: str) -> None:
        super().__init__(c)
        self._sn, self._naam = sn, naam
        self._attr_name = NAMEN[naam]
        self._attr_unique_id = f"{sn}_detectie_{naam}"
        self._attr_device_info = _apparaat_info(c, sn)
        self._attr_device_class = KLASSEN.get(naam)
        if naam in ICONEN:
            self._attr_icon = ICONEN[naam]

    @property
    def is_on(self) -> bool:
        return self.coordinator.detectie_aan(self._sn, self._naam)

    @property
    def extra_state_attributes(self) -> dict:
        g = self.coordinator.laatste_gebeurtenis.get(self._sn) or {}
        uit = {"laatste_soort": g.get("soort"), "persoon": g.get("persoon"), "bron": "lokaal (HomeBase)"}
        if self._naam == "persoon":
            # v0.8.0: persoon komt uit de eigen beeldherkenning (de HomeBase zegt het lokaal niet)
            aan = self.coordinator.herkent(self._sn)
            uit["herkenning"] = aan
            if aan:
                h = self.coordinator.herkenning.get(self._sn) or {}
                uit.update({"zekerheid": h.get("zekerheid"), "aantal": h.get("aantal"),
                            "namen": h.get("namen") or [], "onbekend": h.get("onbekend") or 0,
                            "bron": "beeldherkenning in Home Assistant"})
        return uit
