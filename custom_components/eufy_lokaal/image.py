"""Stap 5: het laatste beeld per camera, lokaal van de HomeBase."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EufyLokaalCoordinator
from .sensor import _apparaat_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    c: EufyLokaalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([*(LaatsteBeeld(c, sn) for sn in c.apparaten if sn not in c.stations),
                        *(LiveMomentopname(c, sn) for sn in c.apparaten if sn not in c.stations)])


class LaatsteBeeld(CoordinatorEntity[EufyLokaalCoordinator], ImageEntity):
    _attr_has_entity_name = True
    _attr_name = "Laatste beeld"
    _attr_content_type = "image/jpeg"

    def __init__(self, c: EufyLokaalCoordinator, sn: str) -> None:
        CoordinatorEntity.__init__(self, c)
        ImageEntity.__init__(self, c.hass)
        self._sn = sn
        self._attr_unique_id = f"{sn}_laatste_beeld"
        self._attr_device_info = _apparaat_info(c, sn)
        self._tijd: float | None = None

    @property
    def available(self) -> bool:
        return self._sn in self.coordinator.beelden

    @callback
    def _handle_coordinator_update(self) -> None:
        b = self.coordinator.beelden.get(self._sn)
        if b and b["tijd"] != self._tijd:
            self._tijd = b["tijd"]
            self._attr_image_last_updated = datetime.fromtimestamp(b["tijd"], timezone.utc)
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        b = self.coordinator.beelden.get(self._sn)
        return b["data"] if b else None

    @property
    def extra_state_attributes(self) -> dict:
        b = self.coordinator.beelden.get(self._sn) or {}
        i = self.coordinator.laatste_info.get(self._sn) or {}
        return {"bestand": b.get("pad"), "grootte": b.get("grootte"),
                "gebeurtenissen": i.get("gebeurtenissen"), "bron": "lokaal (HomeBase)"}


class LiveMomentopname(LaatsteBeeld):
    """Het laatste live beeld (bij aanbellen, of via eufy_lokaal.momentopname)."""

    _attr_name = "Live momentopname"

    def __init__(self, c, sn) -> None:
        super().__init__(c, sn)
        self._attr_unique_id = f"{sn}_live_momentopname"
        # v0.7.17: een bewaard beeld van voor de herstart is meteen zichtbaar
        m = c.momentopnamen.get(sn) or {}
        if m.get("data"):
            self._tijd = m["tijd"]
            self._attr_image_last_updated = datetime.fromtimestamp(m["tijd"], timezone.utc)

    @property
    def available(self) -> bool:
        return bool((self.coordinator.momentopnamen.get(self._sn) or {}).get("data"))

    @callback
    def _handle_coordinator_update(self) -> None:
        m = self.coordinator.momentopnamen.get(self._sn)
        if m and m.get("data") and m["tijd"] != self._tijd:
            self._tijd = m["tijd"]
            self._attr_image_last_updated = datetime.fromtimestamp(m["tijd"], timezone.utc)
        CoordinatorEntity._handle_coordinator_update(self)

    async def async_image(self) -> bytes | None:
        return (self.coordinator.momentopnamen.get(self._sn) or {}).get("data")

    @property
    def extra_state_attributes(self) -> dict:
        m = self.coordinator.momentopnamen.get(self._sn) or {}
        uit = {k: m.get(k) for k in ("reden", "duur_s", "grootte", "fout")}
        uit["bewaard"] = bool(m.get("bewaard"))
        return uit
