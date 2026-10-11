"""Sensoren van stap 1: per station of het lokaal te vinden is, firmware en
adres; per apparaat batterij en wifi waar de cloud die kent; en één
meetsensor "Eufy lokaal" met de samenvatting."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cloud import PARAM_BATTERIJ, PARAM_WIFI_RSSI, parameter
from .const import DOMAIN
from .coordinator import EufyLokaalCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    c: EufyLokaalCoordinator = hass.data[DOMAIN][entry.entry_id]
    entiteiten: list[SensorEntity] = [EufyLokaalSensor(c, entry)]
    for sn in c.stations:
        entiteiten += [StationLokaalSensor(c, sn), StationFirmwareSensor(c, sn), StationIpSensor(c, sn)]
    for sn, a in c.apparaten.items():
        if parameter(a, PARAM_BATTERIJ) is not None:
            entiteiten.append(ApparaatBatterijSensor(c, sn))
        if parameter(a, PARAM_WIFI_RSSI) is not None:
            entiteiten.append(ApparaatWifiSensor(c, sn))
    async_add_entities(entiteiten)


def _station_info(c: EufyLokaalCoordinator, sn: str) -> DeviceInfo:
    s = c.stations.get(sn) or {}
    return DeviceInfo(
        identifiers={(DOMAIN, sn)}, name=s.get("station_name") or sn, manufacturer="Eufy",
        model=s.get("station_model"), sw_version=s.get("main_sw_version"), serial_number=sn,
    )


def _apparaat_info(c: EufyLokaalCoordinator, sn: str) -> DeviceInfo:
    a = c.apparaten.get(sn) or {}
    info = DeviceInfo(
        identifiers={(DOMAIN, sn)}, name=a.get("device_name") or sn, manufacturer="Eufy",
        model=a.get("device_model"), sw_version=a.get("main_sw_version"), serial_number=sn,
    )
    station = a.get("station_sn")
    if station and station != sn and station in c.stations:
        info["via_device"] = (DOMAIN, station)
    return info


class _Basis(CoordinatorEntity[EufyLokaalCoordinator], SensorEntity):
    _attr_has_entity_name = True


class EufyLokaalSensor(_Basis):
    """De meetsensor: hoe ver Eufy Lokaal is en hoe goed het werkt."""

    _attr_name = "Eufy lokaal"
    _attr_icon = "mdi:shield-home-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, c: EufyLokaalCoordinator, entry: ConfigEntry) -> None:
        super().__init__(c)
        self._attr_unique_id = f"{entry.entry_id}_samenvatting"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name="Eufy Lokaal", manufacturer="Roedie84",
            model="Eigen integratie",
        )

    @property
    def native_value(self) -> str:
        s = self.coordinator.samenvatting()
        if not s["stations"]:
            return "geen stations"
        if not s["zoekrondes"]:
            return "zoekt"
        p = s["lokaal_gevonden_procent"]
        return "lokaal bereikbaar" if p and p >= 95 else ("deels bereikbaar" if p else "niet lokaal gevonden")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.samenvatting()


class _Station(_Basis):
    def __init__(self, c: EufyLokaalCoordinator, sn: str, sleutel: str) -> None:
        super().__init__(c)
        self._sn = sn
        self._attr_unique_id = f"{sn}_{sleutel}"
        self._attr_device_info = _station_info(c, sn)

    @property
    def _s(self) -> dict:
        return self.coordinator.stations.get(self._sn) or {}


class StationLokaalSensor(_Station):
    _attr_name = "Lokaal"
    _attr_icon = "mdi:lan-connect"

    def __init__(self, c, sn) -> None:
        super().__init__(c, sn, "lokaal")

    @property
    def native_value(self) -> str:
        l = self.coordinator.lokaal.get(self._sn)
        if not l:
            return "zoekt"
        return "gevonden" if l.get("gevonden") else "niet gevonden"

    @property
    def extra_state_attributes(self) -> dict:
        l = dict(self.coordinator.lokaal.get(self._sn) or {})
        l["p2p_did_bekend"] = bool(self._s.get("p2p_did"))
        return l


class StationFirmwareSensor(_Station):
    _attr_name = "Firmware"
    _attr_icon = "mdi:chip"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, c, sn) -> None:
        super().__init__(c, sn, "firmware")

    @property
    def native_value(self) -> str | None:
        return self._s.get("main_sw_version")


class StationIpSensor(_Station):
    _attr_name = "IP-adres"
    _attr_icon = "mdi:ip-network"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, c, sn) -> None:
        super().__init__(c, sn, "ip")

    @property
    def native_value(self) -> str | None:
        from .coordinator import lan_ip

        l = self.coordinator.lokaal.get(self._sn) or {}
        return l.get("ip") or lan_ip(self._s)

    @property
    def extra_state_attributes(self) -> dict:
        return {"publiek_ip": self._s.get("ip_addr")}


class _Apparaat(_Basis):
    def __init__(self, c: EufyLokaalCoordinator, sn: str, sleutel: str) -> None:
        super().__init__(c)
        self._sn = sn
        self._attr_unique_id = f"{sn}_{sleutel}"
        self._attr_device_info = _apparaat_info(c, sn)

    @property
    def _a(self) -> dict:
        return self.coordinator.apparaten.get(self._sn) or {}


class ApparaatBatterijSensor(_Apparaat):
    _attr_name = "Batterij"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, c, sn) -> None:
        super().__init__(c, sn, "batterij")

    @property
    def native_value(self) -> int | None:
        w = parameter(self._a, PARAM_BATTERIJ)
        return w if isinstance(w, int) else None


class ApparaatWifiSensor(_Apparaat):
    _attr_name = "Wifi-signaal"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, c, sn) -> None:
        super().__init__(c, sn, "wifi")

    @property
    def native_value(self) -> int | None:
        w = parameter(self._a, PARAM_WIFI_RSSI)
        return w if isinstance(w, int) else None
