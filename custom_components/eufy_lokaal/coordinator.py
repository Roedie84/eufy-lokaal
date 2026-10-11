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
from .berichten import KANAAL_BINNEN, KANAAL_STATION, PARAM_ALARMSTAND, PARAM_BEWAKINGSSTAND, alarmstand_naam
from .p2p_sessie import P2PSessie
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
        self.sessies: dict[str, P2PSessie] = {}
        self._ciphers: dict[int, str] = {}
        # stap 3: wat de HomeBase lokaal vertelt
        self.lokale_params: dict[str, dict[int, dict[int, str]]] = {}
        self.alarmstand: dict[str, dict] = {}
        self.laatste_melding: dict[str, dict] = {}
        self.berichten_per_soort: dict[str, int] = {}

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
        await self._houd_sessies()
        self.async_update_listeners()

    # stap 2: de P2P-sessie ------------------------------------------------------------

    async def _houd_sessies(self) -> None:
        """Per gevonden station een sessie; bij uitval opnieuw verbinden."""
        for sn, l in self.lokaal.items():
            if not l.get("gevonden"):
                continue
            s = self.sessies.get(sn)
            # Alleen het IP telt: de poort in het zoekantwoord wisselt per ronde.
            if s is not None and s.levend and s.ip == l["ip"]:
                continue
            meting = s.meting if s is not None else None
            if s is not None:
                if meting.verbonden:
                    meting.uitval += 1
                s.sluit()
            station = self.stations.get(sn) or {}
            # v0.2.1: één meting over alle pogingen heen (tellers lopen door).
            nieuw = P2PSessie(sn, station.get("p2p_did", ""), l["ip"], l["poort"], self._cipher_voor(sn), meting,
                              self._bij_bericht_voor(sn))
            self.sessies[sn] = nieuw
            try:
                await nieuw.verbind()
            except OSError as err:
                nieuw.meting.laatste_fout = f"verbinden: {err}"

    # stap 3: berichten ------------------------------------------------------------------

    def apparaat_bij_kanaal(self, station_sn: str, kanaal: int) -> str | None:
        """Het serienummer bij een kanaal van een station (255/1000 = het station zelf)."""
        if kanaal in (KANAAL_STATION, KANAAL_BINNEN):
            return station_sn
        for sn, a in self.apparaten.items():
            if a.get("station_sn") == station_sn and a.get("device_channel") == kanaal:
                return sn
        return None

    def lokale_parameters(self, sn: str) -> dict[int, str]:
        """Lokale parameters (uit CMD_CAMERA_INFO) van een station of apparaat."""
        if sn in self.stations:
            return (self.lokale_params.get(sn) or {}).get(KANAAL_STATION) or {}
        a = self.apparaten.get(sn) or {}
        kanaal = a.get("device_channel")
        if kanaal is None:
            return {}
        return (self.lokale_params.get(a.get("station_sn") or "") or {}).get(int(kanaal)) or {}

    def _bij_bericht_voor(self, station_sn: str):
        def bij_bericht(b: dict) -> None:
            soort = b.get("soort", "onbekend")
            self.berichten_per_soort[soort] = self.berichten_per_soort.get(soort, 0) + 1
            nu = time.time()
            if soort == "camera_info":
                self.lokale_params[station_sn] = b.get("parameters") or {}
                stand = (self.lokale_params[station_sn].get(KANAAL_STATION) or {}).get(PARAM_ALARMSTAND)
                if stand is not None and alarmstand_naam(stand):
                    vorig = self.alarmstand.get(station_sn) or {}
                    if vorig.get("naam") != alarmstand_naam(stand):
                        self.alarmstand[station_sn] = {"waarde": int(stand), "naam": alarmstand_naam(stand),
                                                       "bron": "camera_info", "sinds": nu}
                    else:
                        vorig["bevestigd"] = nu
            elif soort == "alarmstand":
                self.alarmstand[station_sn] = {"waarde": b["waarde"], "naam": b["alarmstand"],
                                               "bron": "melding station", "sinds": nu}
                # De bewakingsstand (1224) kan mee veranderd zijn: parameters verversen.
                self._ververs_parameters_straks(station_sn)
            if soort in ("ping", "antwoord", "camera_info"):
                self.async_update_listeners()
                return
            apparaat = self.apparaat_bij_kanaal(station_sn, int(b.get("kanaal", KANAAL_STATION)))
            gebeurtenis = {**b, "station_sn": station_sn, "apparaat_sn": apparaat, "tijd": nu}
            self.laatste_melding[station_sn] = gebeurtenis
            self.hass.bus.async_fire(f"{DOMAIN}_bericht", gebeurtenis)
            self.async_update_listeners()
        return bij_bericht

    def _ververs_parameters_straks(self, station_sn: str, na_s: float = 3.0) -> None:
        s = self.sessies.get(station_sn)
        if s is not None:
            self.hass.loop.call_later(na_s, s.vraag_camera_info)

    async def async_zet_bewakingsstand(self, station_sn: str, stand: int) -> None:
        """Stap 4: de bewakingsstand lokaal zetten, zoals de app dat doet."""
        from homeassistant.exceptions import HomeAssistantError

        s = self.sessies.get(station_sn)
        if s is None or not s.levend:
            raise HomeAssistantError("Geen lokale verbinding met de HomeBase")
        lid = (self.stations.get(station_sn) or {}).get("member") or {}
        account = str(lid.get("admin_user_id") or self.cloud.sessie.user_id or "")
        gebruiker = str(lid.get("nick_name") or "Home Assistant")
        try:
            code = await s.zet_bewakingsstand(account, stand, gebruiker)
        except (TimeoutError, ConnectionError) as err:
            raise HomeAssistantError(f"HomeBase antwoordde niet: {err or 'geen antwoord binnen 10 s'}") from err
        self.tellingen["laatste_stand_opdracht"] = {"stand": stand, "returncode": code, "tijd": time.time()}
        if code != 0:
            raise HomeAssistantError(f"HomeBase weigerde de bewakingsstand (code {code})")
        # optimistisch bijwerken; de HomeBase bevestigt met 1151 en de parameters
        params = self.lokale_params.setdefault(station_sn, {}).setdefault(KANAAL_STATION, {})
        params[PARAM_BEWAKINGSSTAND] = str(stand)
        self._ververs_parameters_straks(station_sn)
        self.async_update_listeners()

    def bewakingsstand_waarde(self, station_sn: str) -> int | None:
        w = self.lokale_parameters(station_sn).get(PARAM_BEWAKINGSSTAND)
        try:
            return int(w) if w is not None else None
        except ValueError:
            return None

    def bewakingsstand(self, station_sn: str) -> str | None:
        w = self.lokale_parameters(station_sn).get(PARAM_BEWAKINGSSTAND)
        return alarmstand_naam(w) if w is not None else None

    def _cipher_voor(self, sn: str):
        async def haal(cipher_id: int) -> str | None:
            if cipher_id in self._ciphers:
                return self._ciphers[cipher_id]
            beheerder = str(((self.stations.get(sn) or {}).get("member") or {}).get("admin_user_id") or "")
            sleutel = await self.cloud.cipher(cipher_id, beheerder or (self.cloud.sessie.user_id or ""))
            if sleutel:
                self._ciphers[cipher_id] = sleutel
            return sleutel
        return haal

    def stop(self) -> None:
        for s in self.sessies.values():
            s.sluit()

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
            "stap": "4 - lokale berichten, parameters en bewakingsstand",
            "laatste_stand_opdracht": t.get("laatste_stand_opdracht"),
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
            "p2p": {sn: s.samenvatting() for sn, s in self.sessies.items()},
            "berichten_per_soort": self.berichten_per_soort,
            "lokale_parameters": {sn: {str(k): len(v) for k, v in kanalen.items()}
                                  for sn, kanalen in self.lokale_params.items()},
            "alarmstand": self.alarmstand,
        }
