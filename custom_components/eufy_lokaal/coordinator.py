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
        # stap 5: laatste gebeurtenis, beelden en opnames
        self.laatste_info: dict[str, dict] = {}
        self.beelden: dict[str, dict] = {}
        self.opnames: dict[str, list] = {}
        self._beeld_voor: dict[str, tuple[str, float]] = {}  # bestand op de HomeBase -> (apparaat, gevraagd)
        self._info_ronde = 0
        # stap 6: livestreams
        from .video import nieuw_token

        self.live: dict[str, "LiveStream"] = {}
        # gebeurtenissen per apparaat: soort -> laatste tijd
        self.detecties: dict[str, dict[str, float]] = {}
        self.laatste_gebeurtenis: dict[str, dict] = {}
        self._bij_gebeurtenis: list = []
        self.live_token = nieuw_token()

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
        self._info_ronde += 1
        if self._info_ronde % 5 == 1:  # elke 5 minuten (en meteen na de start)
            for sn in self.sessies:
                self.hass.loop.call_later(15, self._vraag_laatste_info, sn)
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
                m = s.meting
                reden = ("ander IP" if s.ip != l["ip"] else "niet verbonden" if not m.verbonden else
                         f"{round(time.time() - max(m.laatst_iets_ontvangen or 0, m.verbonden_sinds or 0))} s stil")
                lijst = self.tellingen.setdefault("herverbonden", [])
                lijst.append({"t": round(time.time()), "reden": reden})
                del lijst[-6:-6]
                del lijst[:-6]
                if meting.verbonden:
                    meting.uitval += 1
                s.sluit()
            station = self.stations.get(sn) or {}
            # v0.2.1: één meting over alle pogingen heen (tellers lopen door).
            nieuw = P2PSessie(sn, station.get("p2p_did", ""), l["ip"], l["poort"], self._cipher_voor(sn), meting,
                              self._bij_bericht_voor(sn))
            nieuw._bij_video = self._bij_video_voor(sn)
            for stream in self.live.values():  # lopende streams zijn met de oude sessie weg
                if (self.apparaten.get(stream.sn) or {}).get("station_sn") == sn:
                    stream.stop()
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
            if soort == "database":
                lijst = self.tellingen.setdefault("database_antwoorden", [])
                lijst.append({**(b.get("samenvatting") or {}), "t": round(nu)})
                del lijst[:-6]
                self._bij_database(station_sn, b)
                self.async_update_listeners()
                return
            if soort == "afbeelding":
                self._bij_afbeelding(station_sn, b)
                self.async_update_listeners()
                return
            if soort in ("melding", "alarmstand", "vertraagd_alarm", "alarm_ingeschakeld"):
                # na een gebeurtenis staat er vaak een nieuw beeld klaar
                self.hass.loop.call_later(8, self._vraag_laatste_info, station_sn)
            if soort == "antwoord":
                lijst = self.tellingen.setdefault("antwoorden", [])
                lijst.append({"opdracht": b.get("opdracht"), "code": b.get("returncode"), "t": round(nu)})
                del lijst[:-8]
            if soort in ("ping", "antwoord", "camera_info"):
                self.async_update_listeners()
                return
            if soort == "melding":
                from .gebeurtenissen import gebeurtenis_uit

                g = gebeurtenis_uit(b)
                if g is not None:
                    self._gebeurtenis(station_sn, g, nu)
            apparaat = self.apparaat_bij_kanaal(station_sn, int(b.get("kanaal", KANAAL_STATION)))
            gebeurtenis = {**b, "station_sn": station_sn, "apparaat_sn": apparaat, "tijd": nu}
            self.laatste_melding[station_sn] = gebeurtenis
            self.hass.bus.async_fire(f"{DOMAIN}_bericht", gebeurtenis)
            self.async_update_listeners()
        return bij_bericht

    # stap 5: beelden en opnames -------------------------------------------------------

    def _account(self, station_sn: str) -> str:
        lid = (self.stations.get(station_sn) or {}).get("member") or {}
        return str(lid.get("admin_user_id") or self.cloud.sessie.user_id or "")

    def _vraag_laatste_info(self, station_sn: str) -> None:
        from .p2p_sessie import laatste_info_tekst

        s = self.sessies.get(station_sn)
        if s is not None and s.levend:
            s.vraag(laatste_info_tekst(self._account(station_sn), int(time.time() * 1000)), "laatste_info")

    def _vraag_opnames(self, station_sn: str) -> None:
        from homeassistant.util import dt as dt_util

        from .p2p_sessie import opnames_tekst

        s = self.sessies.get(station_sn)
        apparaten = [sn for sn, a in self.apparaten.items() if a.get("station_sn") == station_sn and sn != station_sn]
        if s is None or not s.levend or not apparaten:
            return
        # De einddatum telt niet mee (tot, niet t/m): vandaag tot morgen.
        nu = dt_util.now()
        s.vraag(opnames_tekst(self._account(station_sn), apparaten, nu.strftime("%Y%m%d"),
                              (nu + timedelta(days=1)).strftime("%Y%m%d"),
                              int(time.time() * 1000), aantal=50), "opnames")

    def _bij_database(self, station_sn: str, b: dict) -> None:
        from .p2p_sessie import afbeelding_tekst

        nu = time.time()
        if b.get("sub_opdracht") == 10013:
            nieuw = False
            for r in b.get("data") or []:
                sn = r.get("device_sn")
                p = r.get("payload") or {}
                if not sn:
                    continue
                pad = p.get("crop_hb3_path") or ""
                vorig = self.laatste_info.get(sn) or {}
                self.laatste_info[sn] = {"gebeurtenissen": p.get("event_count"), "pad": pad or None,
                                         "cloud_pad": p.get("crop_cloud_path") or None, "tijd": nu}
                if vorig.get("gebeurtenissen") != p.get("event_count"):
                    nieuw = True
                gevraagd = self._beeld_voor.get(pad)
                if pad and (self.beelden.get(sn) or {}).get("pad") != pad and \
                        (gevraagd is None or nu - gevraagd[1] > 120):
                    s = self.sessies.get(station_sn)
                    if s is not None and s.vraag(afbeelding_tekst(self._account(station_sn), pad), "afbeelding"):
                        self._beeld_voor[pad] = (sn, nu)
            if nieuw or not self.opnames:
                self.hass.loop.call_later(2, self._vraag_opnames, station_sn)
        elif b.get("sub_opdracht") in (10006, 10017):
            per_apparaat: dict[str, dict[int, dict]] = {}
            if b.get("sub_opdracht") == 10006:  # platte lijst
                tabellen = [{"table_name": "history_record_info", "payload": b.get("data") or []}]
            else:
                tabellen = b.get("data") or []
            for tabel in tabellen:
                if tabel.get("table_name") != "history_record_info":
                    continue
                for r in tabel.get("payload") or []:
                    sn = r.get("device_sn")
                    if not sn:
                        continue
                    per_apparaat.setdefault(sn, {})[r.get("record_id")] = {
                        k: r.get(k) for k in ("record_id", "start_time", "end_time", "storage_path",
                                              "thumb_path", "trigger_type", "video_type", "storage_type",
                                              "frame_num", "cloud_path")
                    }
            for sn, records in per_apparaat.items():
                self.opnames[sn] = sorted(records.values(), key=lambda r: str(r.get("start_time")),
                                          reverse=True)[:20]
            for sn in [a for a, x in self.apparaten.items() if x.get("station_sn") == station_sn]:
                self.opnames.setdefault(sn, [])

    def _bij_afbeelding(self, station_sn: str, b: dict) -> None:
        from .berichten import ontsleutel_afbeelding

        pad = b.get("bestand") or ""
        gevraagd = self._beeld_voor.pop(pad, None)
        if gevraagd is None:
            return
        sn = gevraagd[0]
        did = (self.stations.get(station_sn) or {}).get("p2p_did", "")
        data = ontsleutel_afbeelding(did, b.get("ruw") or b"")
        if data[:2] != b"\xff\xd8":
            self.tellingen["laatste_fout"] = (
                f"afbeelding {pad}: geen JPEG ({data[:4].hex()}; b64 {b.get('b64_lengte')} tekens, "
                f"begin {b.get('b64_begin')!r}, fout {b.get('fout')}, velden {b.get('sleutels')})")
            if not data:
                return
        self.beelden[sn] = {"pad": pad, "data": data, "tijd": time.time(), "grootte": len(data)}

    # stap 6: livestream ----------------------------------------------------------------

    def live_stream(self, apparaat_sn: str):
        """De (enige) livestream van een camera, zo nodig aangemaakt."""
        from .video import LiveStream

        if apparaat_sn not in self.live:
            a = self.apparaten.get(apparaat_sn) or {}
            station_sn = a.get("station_sn") or ""
            kanaal = int(a.get("device_channel") or 0)
            self.live[apparaat_sn] = LiveStream(
                apparaat_sn, kanaal,
                lambda sleutel: self._start_live(apparaat_sn, sleutel),
                lambda: self._stop_live(apparaat_sn),
            )
        return self.live[apparaat_sn]

    def _start_live(self, apparaat_sn: str, publieke_sleutel: str) -> None:
        from .video_opdrachten import startopdracht

        a = self.apparaten.get(apparaat_sn) or {}
        station_sn = a.get("station_sn") or ""
        s = self.sessies.get(station_sn)
        if s is None or not s.levend:
            raise ConnectionError("geen lokale verbinding met de HomeBase")
        opdracht, tekst, route = startopdracht(
            int(a.get("device_type") or 0), int(a.get("device_channel") or 0),
            self._account(station_sn), publieke_sleutel, station_sn)
        self.live[apparaat_sn].route = route
        s.start_live(int(a.get("device_channel") or 0), opdracht, tekst)

    def _stop_live(self, apparaat_sn: str) -> None:
        a = self.apparaten.get(apparaat_sn) or {}
        s = self.sessies.get(a.get("station_sn") or "")
        if s is not None:
            s.stop_live(int(a.get("device_channel") or 0))

    def _bij_video_voor(self, station_sn: str):
        from .p2p_sessie import CMD_AUDIO_FRAME, CMD_VIDEO_FRAME

        def bij_video(kop, inhoud: bytes) -> None:
            if kop.opdracht not in (CMD_VIDEO_FRAME, CMD_AUDIO_FRAME):
                return
            for stream in self.live.values():
                if stream.actief and stream.kanaal == kop.kanaal and \
                        (self.apparaten.get(stream.sn) or {}).get("station_sn") == station_sn:
                    if kop.opdracht == CMD_VIDEO_FRAME:
                        stream.bij_frame(inhoud, kop.versleuteld)
                    else:
                        stream.bij_audio(inhoud)
                    return
        return bij_video

    def stream_url(self, apparaat_sn: str) -> str:
        poort = getattr(getattr(self.hass, "http", None), "server_port", None) or 8123
        ssl = bool(getattr(getattr(self.hass.config, "api", None), "use_ssl", False))
        return (f"{'https' if ssl else 'http'}://127.0.0.1:{poort}/api/{DOMAIN}/live/"
                f"{apparaat_sn}?token={self.live_token}")

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

    def _gebeurtenis(self, station_sn: str, g: dict, nu: float) -> None:
        """Beweging, aanbellen e.d.: detectie aan, gebeurtenis op de bus."""
        from .gebeurtenissen import DETECTIES, DUUR_S

        sn = g.get("apparaat_sn") or station_sn
        g = {**g, "station_sn": station_sn, "apparaat_sn": sn, "tijd": nu}
        self.laatste_gebeurtenis[sn] = g
        d = self.detecties.setdefault(sn, {})
        for naam, soorten in DETECTIES.items():
            if g["soort"] in soorten:
                d[naam] = nu
        self.hass.bus.async_fire(f"{DOMAIN}_gebeurtenis", {k: v for k, v in g.items() if k != "ruw"})
        for terug in list(self._bij_gebeurtenis):
            terug(sn, g)
        # na afloop de detecties weer uit
        self.hass.loop.call_later(DUUR_S + 1, self.async_update_listeners)
        self.hass.loop.call_later(4, self._vraag_laatste_info, station_sn)

    def detectie_aan(self, sn: str, naam: str) -> bool:
        from .gebeurtenissen import DUUR_S

        t = (self.detecties.get(sn) or {}).get(naam)
        return t is not None and time.time() - t < DUUR_S

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
        for l in self.live.values():
            l.stop()
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
            "antwoorden": t.get("antwoorden"),
            "database_antwoorden": t.get("database_antwoorden"),
            "herverbonden": t.get("herverbonden"),
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
            "beelden": {sn: {k: v for k, v in b.items() if k != "data"} for sn, b in self.beelden.items()},
            "opnames": {sn: len(v) for sn, v in self.opnames.items()},
            "live": {sn: {**l.samenvatting(), "route": getattr(l, "route", None)} for sn, l in self.live.items()},
        }
