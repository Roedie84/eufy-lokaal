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
from .cloud import CaptchaNodig, CloudFout, EMailCodeNodig, EufyCloud, Sessie
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
        adressen: list[str] = []
        for s in self.stations.values():
            for a in (s.get("ip_addr"), broadcast_van(s.get("ip_addr"))):
                if a and a not in adressen:
                    adressen.append(a)
        if not adressen:
            adressen = ["255.255.255.255"]
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
            "lokaal": {
                sn: {**v, "naam": (self.stations.get(sn) or {}).get("station_name")}
                for sn, v in self.lokaal.items()
            },
        }
