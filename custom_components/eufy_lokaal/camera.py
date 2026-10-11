"""Stap 6: camera's met livestream (op vraag) en het laatste beeld."""
from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EufyLokaalCoordinator
from .sensor import _apparaat_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    c: EufyLokaalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(EufyCamera(c, sn) for sn, a in c.apparaten.items()
                       if sn not in c.stations and a.get("device_channel") is not None)


class EufyCamera(CoordinatorEntity[EufyLokaalCoordinator], Camera):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, c: EufyLokaalCoordinator, sn: str) -> None:
        CoordinatorEntity.__init__(self, c)
        Camera.__init__(self)
        self._sn = sn
        self._attr_unique_id = f"{sn}_camera"
        self._attr_device_info = _apparaat_info(c, sn)

    @property
    def available(self) -> bool:
        a = self.coordinator.apparaten.get(self._sn) or {}
        s = self.coordinator.sessies.get(a.get("station_sn") or "")
        return s is not None and s.levend

    @property
    def is_streaming(self) -> bool:
        l = self.coordinator.live.get(self._sn)
        return bool(l and l.actief)

    async def stream_source(self) -> str | None:
        return self.coordinator.stream_url(self._sn)

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        b = self.coordinator.beelden.get(self._sn)
        return b["data"] if b else None

    @property
    def extra_state_attributes(self) -> dict:
        l = self.coordinator.live.get(self._sn)
        return {"live": l.samenvatting() if l else None, "bron": "lokaal (HomeBase)"}
