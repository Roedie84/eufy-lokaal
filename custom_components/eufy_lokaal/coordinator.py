"""De coördinator: wat de cloud over de apparaten weet, en of de stations
lokaal te vinden zijn (stap 1)."""
from __future__ import annotations

import logging
import statistics
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import p2p
from .cloud import PARAM_LAN_IP, CaptchaNodig, CloudFout, EMailCodeNodig, EufyCloud, Sessie, parameter
from .const import (
    CLOUD_INTERVAL_S,
    CONF_EMAIL,
    CONF_LAND,
    CONF_SESSIE,
    CONF_WACHTWOORD,
    DOMAIN,
    LOKAAL_INTERVAL_S,
)

_LOGGER = logging.getLogger(__name__)


def prive_ip(ip) -> bool:
    """Alleen thuisnetadressen (de cloud geeft in `ip_addr` het publieke IP)."""
    import ipaddress

    try:
        return ipaddress.ip_address(str(ip)).is_private
    except ValueError:
        return False


def lan_ip(station: dict) -> str | None:
    """Het LAN-adres van het station: parameter 1176 (CMD_GET_HUB_LAN_IP)."""
    for w in (parameter(station, PARAM_LAN_IP), station.get("ip_addr")):
        if w and prive_ip(w):
            return str(w)
    return None


def zoekadressen(stations: list[dict], eigen_broadcast: list[str]) -> list[str]:
    """Waar de zoekvraag heen gaat: het LAN-adres van elk station en zijn
    broadcast, de broadcastadressen van Home Assistant zelf, en
    255.255.255.255. Nooit een publiek adres."""
    uit: list[str] = []
    for s in stations:
        ip = lan_ip(s)
        for a in (ip, broadcast_van(ip)):
            if a and a not in uit:
                uit.append(a)
    for a in [*eigen_broadcast, "255.255.255.255"]:
        if a and a not in uit:
            uit.append(a)
    return uit


def broadcast_van(ip: str | None) -> str | None:
    """192.168.2.70 -> 192.168.2.255 (een /24, zoals bij vrijwel elk thuisnet)."""
    if not ip or ip.count(".") != 3:
        return None
    return ip.rsplit(".", 1)[0] + ".255"


class EufyLokaalCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=CLOUD_INTERVAL_S)
        )
        self.entry = entry
        self.cloud = EufyCloud(
            async_get_clientsession(hass),
            entry.data[CONF_LAND], entry.data[CONF_EMAIL], entry.data[CONF_WACHTWOORD],
            Sessie.uit(entry.data.get(CONF_SESSIE)),
        )
        self.stations: dict[str, dict] = {}
        self.apparaten: dict[str, dict] = {}
        self.lokaal: dict[str, dict] = {}
        self.tellingen = {"zoekrondes": 0, "gevonden": 0, "niet_gevonden": 0, "ms": [],
                          "cloud_rondes": 0, "cloud_fouten": 0, "laatste_fout": None}
        self._afmelden = None
        self._laatste_adressen: list[str] = []

    # cloud ----------------------------------------------------------------------

    async def _async_update_data(self) -> dict:
        voor = self.cloud.sessie.naar()
        try:
            stations = await self.cloud.stations()
            apparaten = await self.cloud.apparaten()
        except (EMailCodeNodig, CaptchaNodig) as err:
            raise ConfigEntryAuthFailed(f"Eufy vraagt opnieuw om bevestiging: {err}") from err
        except CloudFout as err:
            self.tellingen["cloud_fouten"] += 1
            self.tellingen["laatste_fout"] = f"{err} (code {err.code})"
            if err.code in (26006, 10019, 100056):
                raise ConfigEntryAuthFailed(str(err)) from err
            raise UpdateFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001 - netwerk e.d.
            self.tellingen["cloud_fouten"] += 1
            self.tellingen["laatste_fout"] = f"{type(err).__name__}: {err}"
            raise UpdateFailed(str(err)) from err
        finally:
            if self.cloud.sessie.naar() != voor:
                self.hass.config_entries.async_update_entry(
                    self.entry, data={**self.entry.data, CONF_SESSIE: self.cloud.sessie.naar()}
                )
        self.tellingen["cloud_rondes"] += 1
        self.stations = {s["station_sn"]: s for s in stations if s.get("station_sn")}
        self.apparaten = {a["device_sn"]: a for a in apparaten if a.get("device_sn")}
        return {"stations": self.stations, "apparaten": self.apparaten, "tijd": time.time()}

    # lokaal ---------------------------------------------------------------------

    def start_lokaal(self) -> None:
        self._afmelden = async_track_time_interval(
            self.hass, self._zoek_lokaal, timedelta(seconds=LOKAAL_INTERVAL_S)
        )
        self.entry.async_on_unload(self._afmelden)
        self.hass.async_create_background_task(self._zoek_lokaal(), f"{DOMAIN}_eerste_zoektocht")

    async def _zoek_lokaal(self, _nu=None) -> None:
        verwacht = {s.get("p2p_did"): sn for sn, s in self.stations.items() if s.get("p2p_did")}
        if not verwacht:
            return
        adressen = zoekadressen(list(self.stations.values()), await self._eigen_broadcast())
        self._laatste_adressen = adressen
        try:
            gevonden = await p2p.zoek(adressen, set(verwacht))
        except OSError as err:
            self.tellingen["laatste_fout"] = f"zoeken: {err}"
            return
        self.tellingen["zoekrondes"] += 1
        nu = time.time()
        for did, sn in verwacht.items():
            g = gevonden.get(did)
            vorig = self.lokaal.get(sn) or {}
            if g is None:
                self.tellingen["niet_gevonden"] += 1
                self.lokaal[sn] = {**vorig, "gevonden": False, "laatst_gezocht": nu}
                continue
            self.tellingen["gevonden"] += 1
            ms = self.tellingen["ms"]
            ms.append(g.ms)
            del ms[:-200]
            self.lokaal[sn] = {
                "gevonden": True, "ip": g.ip, "poort": g.poort, "ms": g.ms,
                "laatst_gezien": nu, "laatst_gezocht": nu,
            }
        self.async_update_listeners()

    async def _eigen_broadcast(self) -> list[str]:
        """De broadcastadressen van de netwerkkaarten van Home Assistant."""
        try:
            from homeassistant.components import network

            return [str(a) for a in await network.async_get_ipv4_broadcast_addresses(self.hass)
                    if str(a) != "255.255.255.255"]
        except Exception:  # noqa: BLE001
            return []

    def samenvatting(self) -> dict[str, Any]:
        t = self.tellingen
        ms = t["ms"]
        rondes = t["zoekrondes"] * max(1, len([s for s in self.stations.values() if s.get("p2p_did")]))
        return {
            "stap": "1 - cloud-inventaris en lokaal vinden (alleen lezen)",
            "stations": len(self.stations),
            "apparaten": len(self.apparaten),
            "cloud_rondes": t["cloud_rondes"],
            "cloud_fouten": t["cloud_fouten"],
            "zoekrondes": t["zoekrondes"],
            "lokaal_gevonden_procent": round(100 * t["gevonden"] / rondes, 1) if rondes else None,
            "antwoordtijd_mediaan_ms": round(statistics.median(ms), 1) if ms else None,
            "token_geldig_tot": self.cloud.sessie.token_tot,
            "laatste_fout": t["laatste_fout"],
            "zoekadressen": self._laatste_adressen,
            "lokaal": {
                sn: {**v, "naam": (self.stations.get(sn) or {}).get("station_name")}
                for sn, v in self.lokaal.items()
            },
        }
