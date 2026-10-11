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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .berichten import ALARMSTANDEN
from .cloud import PARAM_BATTERIJ, PARAM_WIFI_RSSI, parameter
from .const import DOMAIN
from .coordinator import EufyLokaalCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    c: EufyLokaalCoordinator = hass.data[DOMAIN][entry.entry_id]
    entiteiten: list[SensorEntity] = [EufyLokaalSensor(c, entry)]
    for sn in c.stations:
        entiteiten += [StationLokaalSensor(c, sn), StationP2PSensor(c, sn), StationFirmwareSensor(c, sn),
                       StationIpSensor(c, sn), StationAlarmstandSensor(c, sn), StationMeldingSensor(c, sn),
                       ParametersSensor(c, sn, station=True)]
    for sn, a in c.apparaten.items():
        if sn not in c.stations:
            entiteiten += [ParametersSensor(c, sn, station=False), OpnamesSensor(c, sn)]
        if parameter(a, PARAM_BATTERIJ) is not None:
            entiteiten.append(ApparaatBatterijSensor(c, sn))
        if parameter(a, PARAM_WIFI_RSSI) is not None:
            entiteiten.append(ApparaatWifiSensor(c, sn))
    async_add_entities(entiteiten)

    # Elke lokale parameter als eigen entiteit; ze komen pas na de eerste
    # CMD_CAMERA_INFO binnen, dus bij elke update kijken wat er nieuw is.
    bekend: set[tuple[str, int]] = set()

    @callback
    def _nieuwe_parameters() -> None:
        nieuw = []
        for sn in list(c.stations) + [s for s in c.apparaten if s not in c.stations]:
            for nummer in c.lokale_parameters(sn):
                if (sn, nummer) not in bekend:
                    bekend.add((sn, nummer))
                    nieuw.append(ParameterSensor(c, sn, nummer))
        if nieuw:
            async_add_entities(nieuw)

    _nieuwe_parameters()
    entry.async_on_unload(c.async_add_listener(_nieuwe_parameters))


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
    # Geen via_device meer (verouderd in Home Assistant; geeft een fout bij
    # platforms die buiten de integratie-frame worden toegevoegd).
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


class StationP2PSensor(_Station):
    """Stap 2: de lokale, versleutelde P2P-sessie met het station."""

    _attr_name = "P2P"
    _attr_icon = "mdi:lock-check-outline"

    def __init__(self, c, sn) -> None:
        super().__init__(c, sn, "p2p")

    @property
    def _sessie(self):
        return self.coordinator.sessies.get(self._sn)

    @property
    def native_value(self) -> str:
        s = self._sessie
        if s is None:
            return "wacht op lokaal vinden"
        sv = s.samenvatting()
        if not sv["verbonden"]:
            return "niet verbonden"
        return "verbonden, versleuteld" if s.meting.niveau else "verbonden"

    @property
    def extra_state_attributes(self) -> dict:
        s = self._sessie
        return s.samenvatting() if s is not None else {}


class StationAlarmstandSensor(_Station):
    """Stap 3: de alarmstand zoals de HomeBase die lokaal meldt."""

    _attr_name = "Alarmstand"
    _attr_icon = "mdi:shield-home"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = sorted(set(ALARMSTANDEN.values()))

    def __init__(self, c, sn) -> None:
        super().__init__(c, sn, "alarmstand")

    @property
    def native_value(self) -> str | None:
        naam = (self.coordinator.alarmstand.get(self._sn) or {}).get("naam")
        return naam if naam in self._attr_options else None

    @property
    def extra_state_attributes(self) -> dict:
        a = dict(self.coordinator.alarmstand.get(self._sn) or {})
        a["bewakingsstand"] = self.coordinator.bewakingsstand(self._sn)
        return a


class StationMeldingSensor(_Station):
    """Stap 3: het laatste bericht dat de HomeBase uit zichzelf stuurde.
    Elk bericht gaat ook als gebeurtenis `eufy_lokaal_bericht` de bus op."""

    _attr_name = "Laatste melding"
    _attr_icon = "mdi:message-badge-outline"

    def __init__(self, c, sn) -> None:
        super().__init__(c, sn, "melding")

    @property
    def native_value(self) -> str | None:
        m = self.coordinator.laatste_melding.get(self._sn)
        return m.get("naam") if m else None

    @property
    def extra_state_attributes(self) -> dict:
        return dict(self.coordinator.laatste_melding.get(self._sn) or {})


class ParametersSensor(_Basis):
    """Stap 3: alle parameters die de HomeBase lokaal over dit apparaat geeft."""

    _attr_name = "Lokale parameters"
    _attr_icon = "mdi:format-list-numbered"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"parameters"})

    def __init__(self, c, sn, station: bool) -> None:
        super().__init__(c)
        self._sn = sn
        self._attr_unique_id = f"{sn}_parameters"
        self._attr_device_info = _station_info(c, sn) if station else _apparaat_info(c, sn)

    @property
    def native_value(self) -> int:
        return len(self.coordinator.lokale_parameters(self._sn))

    @property
    def extra_state_attributes(self) -> dict:
        return {"parameters": {str(k): v for k, v in sorted(self.coordinator.lokale_parameters(self._sn).items())}}


class OpnamesSensor(_Basis):
    """Stap 5: opnames van vandaag op de HomeBase."""

    _attr_name = "Opnames"
    _attr_icon = "mdi:filmstrip-box-multiple"
    _attr_native_unit_of_measurement = "opnames"
    _unrecorded_attributes = frozenset({"opnames"})

    def __init__(self, c, sn) -> None:
        super().__init__(c)
        self._sn = sn
        self._attr_unique_id = f"{sn}_opnames"
        self._attr_device_info = _apparaat_info(c, sn)

    @property
    def available(self) -> bool:
        return self._sn in self.coordinator.opnames

    @property
    def native_value(self) -> int:
        return len(self.coordinator.opnames.get(self._sn) or [])

    @property
    def extra_state_attributes(self) -> dict:
        o = self.coordinator.opnames.get(self._sn) or []
        return {"laatste_start": o[0].get("start_time") if o else None,
                "laatste_einde": o[0].get("end_time") if o else None,
                "opnames": o[:10]}


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
        return _lokaal_of_cloud(self, PARAM_BATTERIJ)

    @property
    def extra_state_attributes(self) -> dict:
        return {"bron": _bron(self, PARAM_BATTERIJ)}


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
        return _lokaal_of_cloud(self, PARAM_WIFI_RSSI)

    @property
    def extra_state_attributes(self) -> dict:
        return {"bron": _bron(self, PARAM_WIFI_RSSI)}


def _lokaal_getal(e: _Apparaat, param: int) -> int | None:
    w = e.coordinator.lokale_parameters(e._sn).get(param)
    try:
        return int(w) if w is not None else None
    except ValueError:
        return None


def _lokaal_of_cloud(e: _Apparaat, param: int) -> int | None:
    """Stap 3: de lokale waarde als de HomeBase die gaf, anders de cloud."""
    w = _lokaal_getal(e, param)
    if w is not None:
        return w
    w = parameter(e._a, param)
    return w if isinstance(w, int) else None


def _bron(e: _Apparaat, param: int) -> str:
    return "lokaal" if _lokaal_getal(e, param) is not None else "cloud"


class ParameterSensor(_Basis):
    """Eén parameter van station of camera, zoals de HomeBase hem lokaal geeft."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"waarde"})

    def __init__(self, c, sn: str, nummer: int) -> None:
        from .parameternamen import parameternaam

        super().__init__(c)
        self._sn, self._nummer = sn, nummer
        self._attr_name = parameternaam(nummer)
        self._attr_unique_id = f"{sn}_param_{nummer}"
        self._attr_device_info = _station_info(c, sn) if sn in c.stations else _apparaat_info(c, sn)

    @property
    def _waarde(self) -> str | None:
        return self.coordinator.lokale_parameters(self._sn).get(self._nummer)

    @property
    def available(self) -> bool:
        return self._waarde is not None

    @property
    def native_value(self):
        w = self._waarde
        if w is None:
            return None
        try:
            return int(w)
        except ValueError:
            pass
        try:
            return float(w)
        except ValueError:
            pass
        return w if len(w) <= 250 else f"{w[:240]}… ({len(w)} tekens)"

    @property
    def extra_state_attributes(self) -> dict:
        w = self._waarde or ""
        a = {"parameter": self._nummer}
        if len(w) > 250 or w.startswith(("{", "[")):
            a["waarde"] = w
        return a
