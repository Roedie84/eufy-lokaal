"""Stap 2 van Eufy Lokaal: een lokale, versleutelde P2P-sessie met het station.

Volgt de openbare beschrijving zoals eufy-security-client (MIT, bropat) het
protocol implementeert; geen code overgenomen.

Verloop:

1. CHECK_CAM (`f1 41`, inhoud: p2p_did + `00 00 00`) naar het adres en de
   poort uit de lokale zoektocht (stap 1), plus de 3 poorten erboven en
   eronder;
2. het station antwoordt met CAM_ID (`f1 42`): verbonden. Vanaf dan elke
   5 s een PING (`f1 e0`), en elke PING van het station krijgt een PONG;
3. de eerste opdracht is CMD_GATEWAYINFO (1100). Het antwoord bevat een
   cipher-id en een met RSA versleutelde sessiesleutel. De bijbehorende
   RSA-sleutel haalt de integratie één keer uit de cloud
   (`v2/app/cipher/get_ciphers`); daarmee wordt de sessiesleutel
   ontsleuteld (versleuteling niveau 2). Lukt dat niet, dan niveau 1: een
   sleutel uit serienummer en p2p_did;
4. elk DATA-pakket (`f1 d0`) van het station wordt bevestigd met een ACK
   (`f1 d1`) en ingelezen: 16 bytes kop ("XZYH", opdracht, lengte, kanaal,
   versleuteld, soort) en de inhoud, eventueel over meerdere pakketten.

5. stap 3: is de sleutel er, dan vraagt de sessie CMD_CAMERA_INFO (1103,
   alleen lezen: alle parameters van station en apparaten) en daarna elke
   10 minuten opnieuw. Berichten die het station uit zichzelf stuurt
   (alarmstand, meldingen) gaan via `bij_bericht` naar de coördinator.
   Berichten op het CONTROL-kanaal zijn met niveau 1 versleuteld, antwoorden
   op het DATA-kanaal met de sessiesleutel.

6. stap 4: de bewakingsstand instellen. Op deze firmware via CMD_SET_PAYLOAD
   (1350) met JSON `{"account_id", "cmd": 1224, "mValue3": 0, "payload":
   {"mode_type", "user_name"}}`, versleuteld. Het station antwoordt met een
   returncode (0 = gelukt) en meldt daarna de nieuwe alarmstand (1151).

Het enige dat deze integratie verandert is de bewakingsstand. Alleen
CHECK_CAM, PING/PONG, ACK, CMD_GATEWAYINFO, CMD_CAMERA_INFO en
CMD_SET_PAYLOAD met daarin uitsluitend CMD_SET_ARMING. Een broncodetoets
bewaakt dat.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .p2p import bericht, did_naar_bytes

_LOGGER = logging.getLogger(__name__)

CHECK_CAM = b"\xf1\x41"
CAM_ID = b"\xf1\x42"
PING = b"\xf1\xe0"
PONG = b"\xf1\xe1"
DATA = b"\xf1\xd0"
ACK = b"\xf1\xd1"
END = b"\xf1\xf0"

SOORT_DATA = 0
SOORT_VIDEO = 1
SOORT_CONTROL = 2
SOORT_BINARY = 3

MAGIC = b"XZYH"
KOP = 16
KANAAL_STATION = 255

CMD_GATEWAYINFO = 1100
CMD_CAMERA_INFO = 1103
CMD_PING = 1139
CMD_SET_ARMING = 1224
CMD_SET_PAYLOAD = 1350
BEWAKINGSSTANDEN = frozenset({0, 1, 2, 3, 4, 5, 47, 63})
CMD_DATABASE = 1306
CMD_DATABASE_IMAGE = 1308
# Stap 5: alleen opvragen uit de database van de HomeBase, nooit wissen.
DATABASE_VRAGEN = frozenset({10013, 10017})  # laatste info, opnames per datum

HARTSLAG_S = 5.0
MAX_ZONDER_PONG_S = 30.0
VERBIND_TIMEOUT_S = 10.0

TOEGESTANE_OPDRACHTEN = frozenset({CMD_GATEWAYINFO})
TOEGESTANE_OPDRACHTEN_MET_GETAL = frozenset({CMD_CAMERA_INFO})
# Opdrachten die het station versleuteld verwacht (deel van de lijst van
# eufy-security-client; alleen wat deze integratie stuurt).
VERSLEUTELDE_OPDRACHTEN = frozenset({CMD_CAMERA_INFO, CMD_SET_PAYLOAD})
ANTWOORD_TIMEOUT_S = 10.0
CAMERA_INFO_ELKE_S = 600.0
OPNIEUW_NA_S = 1.0
MAX_OPNIEUW = 4


# --- zuiver: pakketten ---------------------------------------------------------


def check_cam(did: str) -> bytes:
    return bericht(CHECK_CAM, did_naar_bytes(did) + b"\x00\x00\x00")


def ack(soort: int, volgnr: int) -> bytes:
    return bericht(ACK, bytes([0xD1, soort]) + struct.pack(">HH", 1, volgnr))


def opdracht_zonder_data(volgnr: int, opdracht: int, kanaal: int = KANAAL_STATION) -> bytes:
    """Een DATA-pakket met een opdracht zonder inhoud (CMD_GATEWAYINFO e.d.)."""
    if opdracht not in TOEGESTANE_OPDRACHTEN:
        raise ValueError(f"opdracht {opdracht} is in deze stap niet toegestaan")
    kop = bytes([0xD1, SOORT_DATA]) + struct.pack(">H", volgnr) + MAGIC + struct.pack("<H", opdracht)
    inhoud = b"\x00\x00" + b"\x00\x00" + b"\x01\x00" + bytes([kanaal, 0]) + b"\x00\x00"
    return bericht(DATA, kop + inhoud)


def aes_ecb_versleutel(sleutel: bytes, data: bytes) -> bytes:
    e = Cipher(algorithms.AES(sleutel), modes.ECB()).encryptor()
    return e.update(data) + e.finalize()


def opdracht_met_getal(volgnr: int, opdracht: int, waarde: int, sleutel: bytes | None = None,
                       niveau: int = 0, kanaal: int = KANAAL_STATION) -> bytes:
    """Een DATA-pakket met een opdracht en een getal (CMD_CAMERA_INFO met 255).
    Met sleutel en niveau > 0 wordt de inhoud (opgevuld tot 16 bytes)
    versleuteld en staat het niveau in de kop."""
    if opdracht not in TOEGESTANE_OPDRACHTEN_MET_GETAL:
        raise ValueError(f"opdracht {opdracht} is in deze stap niet toegestaan")
    data = struct.pack("<I", waarde)
    versleuteld = opdracht in VERSLEUTELDE_OPDRACHTEN and niveau > 0 and sleutel is not None and len(sleutel) == 16
    if versleuteld:
        data = aes_ecb_versleutel(sleutel, data.ljust(16, b"\x00"))
    kop = bytes([0xD1, SOORT_DATA]) + struct.pack(">H", volgnr) + MAGIC + struct.pack("<H", opdracht)
    inhoud = (struct.pack("<H", len(data)) + b"\x00\x00" + b"\x01\x00"
              + bytes([kanaal & 0xFF, niveau if versleuteld else 0]) + b"\x00\x00" + data)
    return bericht(DATA, kop + inhoud)


def bewakingsstand_tekst(account_id: str, stand: int, gebruiker: str) -> str:
    """De JSON voor CMD_SET_PAYLOAD die de bewakingsstand zet (zoals de app)."""
    if stand not in BEWAKINGSSTANDEN:
        raise ValueError(f"onbekende bewakingsstand {stand}")
    return json.dumps({"account_id": account_id, "cmd": CMD_SET_ARMING, "mValue3": 0,
                       "payload": {"mode_type": stand, "user_name": gebruiker}}, separators=(",", ":"))


def toegestane_payload(j: Any) -> bool:
    """Wat er via CMD_SET_PAYLOAD naar de HomeBase mag."""
    if not isinstance(j, dict):
        return False
    p = j.get("payload")
    if j.get("cmd") == CMD_SET_ARMING:
        return isinstance(p, dict) and p.get("mode_type") in BEWAKINGSSTANDEN
    if j.get("cmd") == CMD_DATABASE:
        return isinstance(p, dict) and p.get("cmd") in DATABASE_VRAGEN
    if j.get("cmd") == CMD_DATABASE_IMAGE:
        return isinstance(p, list) and len(p) == 1 and set(p[0]) == {"file"} and isinstance(p[0]["file"], str)
    return False


def laatste_info_tekst(account_id: str, nu_ms: int) -> str:
    return json.dumps({"account_id": account_id, "cmd": CMD_DATABASE, "mChannel": 0, "mValue3": 0,
                       "payload": {"cmd": 10013, "table": "history_record_info",
                                   "transaction": str(nu_ms)}}, separators=(",", ":"))


def opnames_tekst(account_id: str, serienummers: list[str], begin: str, einde: str, nu_ms: int,
                  aantal: int = 20) -> str:
    """Opnames op de HomeBase tussen twee datums (JJJJMMDD)."""
    return json.dumps({"account_id": account_id, "cmd": CMD_DATABASE, "mChannel": 0, "mValue3": 0,
                       "payload": {"cmd": 10017, "payload": {
                           "count": aantal, "detection_type": 0,
                           "device_info": [{"device_sn": s} for s in serienummers],
                           "end_date": einde, "event_type": 0, "flag": 0, "res_unzip": 1,
                           "start_date": begin, "start_time": f"{begin}000000", "storage_cloud": -1,
                           "ai_type": 0},
                           "table": "history_record_info", "transaction": str(nu_ms)}}, separators=(",", ":"))


def afbeelding_tekst(account_id: str, bestand: str) -> str:
    return json.dumps({"account_id": account_id, "cmd": CMD_DATABASE_IMAGE, "mChannel": KANAAL_STATION,
                       "payload": [{"file": bestand}], "transaction": bestand}, separators=(",", ":"))


def opdracht_met_tekst(volgnr: int, opdracht: int, tekst: str, sleutel: bytes | None = None,
                       niveau: int = 0, kanaal: int = KANAAL_STATION) -> bytes:
    """Een DATA-pakket met een JSON-tekst. Alleen CMD_SET_PAYLOAD met daarin
    de bewakingsstand, een databasevraag (lezen) of een afbeelding opvragen;
    al het andere wordt geweigerd."""
    if opdracht != CMD_SET_PAYLOAD:
        raise ValueError(f"opdracht {opdracht} is niet toegestaan")
    try:
        j = json.loads(tekst)
    except ValueError as err:
        raise ValueError("geen geldige JSON") from err
    if not toegestane_payload(j):
        raise ValueError("deze opdracht is niet toegestaan")
    data = tekst.encode()
    versleuteld = opdracht in VERSLEUTELDE_OPDRACHTEN and niveau > 0 and sleutel is not None and len(sleutel) == 16
    if versleuteld:
        data = aes_ecb_versleutel(sleutel, data.ljust((len(data) + 15) // 16 * 16, b"\x00"))
    kop = bytes([0xD1, SOORT_DATA]) + struct.pack(">H", volgnr) + MAGIC + struct.pack("<H", opdracht)
    inhoud = (struct.pack("<H", len(data)) + b"\x00\x00" + b"\x01\x00"
              + bytes([kanaal & 0xFF, niveau if versleuteld else 0]) + b"\x00\x00" + data)
    return bericht(DATA, kop + inhoud)


@dataclass
class DataKop:
    opdracht: int
    lengte: int
    kanaal: int
    versleuteld: int
    soort: int


def lees_kop(data: bytes) -> DataKop | None:
    if len(data) < KOP or data[:4] != MAGIC:
        return None
    return DataKop(
        opdracht=struct.unpack("<H", data[4:6])[0],
        lengte=struct.unpack("<I", data[6:10])[0],
        kanaal=data[12],
        versleuteld=data[13],
        soort=data[14],
    )


@dataclass
class Bouwer:
    """Zet een bericht in elkaar dat over meerdere DATA-pakketten loopt."""

    kop: DataKop | None = None
    delen: bytes = b""

    def voeg_toe(self, data: bytes) -> list[tuple[DataKop, bytes]]:
        klaar: list[tuple[DataKop, bytes]] = []
        for _ in range(64):
            if not data:
                break
            if self.kop is None:
                kop = lees_kop(data)
                if kop is None:
                    break  # geen begin van een bericht: overslaan
                self.kop, self.delen, data = kop, b"", data[KOP:]
            nodig = self.kop.lengte - len(self.delen)
            self.delen += data[:nodig]
            data = data[nodig:]
            if len(self.delen) >= self.kop.lengte:
                klaar.append((self.kop, self.delen))
                self.kop, self.delen = None, b""
        return klaar


def aes_ecb_ontsleutel(sleutel: bytes, data: bytes) -> bytes:
    d = Cipher(algorithms.AES(sleutel), modes.ECB()).decryptor()
    return d.update(data) + d.finalize()


def niveau1_sleutel(station_sn: str, did: str) -> bytes:
    """Versleuteling niveau 1: laatste 7 tekens serienummer + "-" en 8 tekens
    van de p2p_did na het eerste streepje (16 bytes)."""
    i = did.index("-")
    return (station_sn[-7:] + did[i:i + 9]).encode()


def rsa_ontsleutel(privesleutel: str, data: bytes) -> bytes:
    """De sessiesleutel met de RSA-sleutel uit de cloud (PKCS#8, base64, met
    of zonder PEM-kop; PKCS#1 v1.5)."""
    tekst = privesleutel.replace("\n", "").replace("\r", "")
    for kop in ("-----BEGIN RSA PRIVATE KEY-----", "-----END RSA PRIVATE KEY-----",
                "-----BEGIN PRIVATE KEY-----", "-----END PRIVATE KEY-----"):
        tekst = tekst.replace(kop, "")
    sleutel = serialization.load_der_private_key(base64.b64decode(tekst), password=None)
    return sleutel.decrypt(data, rsa_padding.PKCS1v15())


def gatewayinfo_uit(inhoud: bytes) -> tuple[int, bytes] | None:
    """(cipher-id, versleutelde sleutel) uit het antwoord op CMD_GATEWAYINFO,
    na ontsleutelen met niveau 1: 2 bytes cipher-id (LE), 2 bytes leeg, dan
    de RSA-versleutelde sessiesleutel. Op de HomeBase 3 (firmware 3.8) is het
    antwoord 144 bytes: 4 + 128 (RSA-1024) + opvulling."""
    if len(inhoud) < 8:
        return None
    return struct.unpack("<H", inhoud[:2])[0], inhoud[4:]


def gatewayinfo_kandidaten(inhoud: bytes) -> list[tuple[int, bytes]]:
    """Zonder en met 4-byte returncode vooraan; de cloud beslist welke klopt."""
    uit = []
    for begin in (0,):
        info = gatewayinfo_uit(inhoud[begin:])
        if info is not None and info[0] and info not in uit:
            uit.append(info)
    return uit


def rsa_sessiesleutel(privesleutel: str, versleuteld: bytes) -> bytes:
    """Ontsleutel precies één RSA-blok (de sleutelgrootte bepaalt de lengte)."""
    tekst = privesleutel.replace("\n", "").replace("\r", "")
    for kop in ("-----BEGIN RSA PRIVATE KEY-----", "-----END RSA PRIVATE KEY-----",
                "-----BEGIN PRIVATE KEY-----", "-----END PRIVATE KEY-----"):
        tekst = tekst.replace(kop, "")
    sleutel = serialization.load_der_private_key(base64.b64decode(tekst), password=None)
    blok = sleutel.key_size // 8
    return sleutel.decrypt(versleuteld[:blok], rsa_padding.PKCS1v15())


# --- de sessie -------------------------------------------------------------------


@dataclass
class Meting:
    verbonden: bool = False
    verbonden_sinds: float | None = None
    verbindingen: int = 0
    uitval: int = 0
    pings: int = 0
    pongs: int = 0
    rtt_ms: list = field(default_factory=list)
    niveau: int = 0
    sleutel_bron: str | None = None
    berichten: int = 0
    per_opdracht: dict = field(default_factory=dict)
    laatste: list = field(default_factory=list)
    laatste_fout: str | None = None
    # v0.2.1: elk pakket per soort, in en uit, en de laatste koppen - om te
    # zien wat het station wel en niet beantwoordt.
    ontvangen: dict = field(default_factory=dict)
    verzonden: dict = field(default_factory=dict)
    laatste_pakketten: list = field(default_factory=list)
    laatst_iets_ontvangen: float | None = None
    # v0.2.2: de inhoud van DATA-pakketten en van elke soort het eerste
    # exemplaar, om het formaat van deze firmware te zien.
    laatste_data: list = field(default_factory=list)
    voorbeelden: dict = field(default_factory=dict)
    sleutel_pogingen: list = field(default_factory=list)
    opdrachten_gestuurd: dict = field(default_factory=dict)

    def data_pakket(self, data: bytes, oordeel: str) -> None:
        self.laatste_data.append({"t": round(time.time(), 1), "lengte": len(data),
                                  "oordeel": oordeel, "inhoud": data[:72].hex()})
        del self.laatste_data[:-12]

    def pakket(self, richting: str, data: bytes) -> None:
        soort = data[:2].hex()
        teller = self.ontvangen if richting == "in" else self.verzonden
        teller[soort] = teller.get(soort, 0) + 1
        if richting == "in":
            self.laatst_iets_ontvangen = time.time()
            self.voorbeelden.setdefault(soort, data[:96].hex())
        self.laatste_pakketten.append(
            {"t": round(time.time(), 1), "r": richting, "soort": soort, "lengte": len(data),
             "begin": data[:16].hex()}
        )
        del self.laatste_pakketten[:-16]

    def noteer(self, kop: DataKop, lengte: int, resultaat: bool) -> None:
        self.berichten += 1
        self.per_opdracht[str(kop.opdracht)] = self.per_opdracht.get(str(kop.opdracht), 0) + 1
        self.laatste.append({"t": round(time.time()), "opdracht": kop.opdracht, "lengte": lengte,
                             "versleuteld": kop.versleuteld, "antwoord": resultaat})
        del self.laatste[:-20]


class _Udp(asyncio.DatagramProtocol):
    def __init__(self, sessie: "P2PSessie") -> None:
        self.sessie = sessie

    def datagram_received(self, data: bytes, adres) -> None:
        try:
            self.sessie.ontvang(data, adres)
        except Exception as err:  # noqa: BLE001 - een vreemd pakket mag niets breken
            self.sessie.meting.laatste_fout = f"pakket: {type(err).__name__}: {err}"

    def error_received(self, exc) -> None:
        self.sessie.meting.laatste_fout = f"udp: {exc}"


class P2PSessie:
    """Eén lokale sessie met één station. Alleen lezen."""

    def __init__(
        self,
        station_sn: str,
        did: str,
        ip: str,
        poort: int,
        haal_cipher: Callable[[int], Awaitable[str | None]] | None = None,
        meting: Meting | None = None,
        bij_bericht: Callable[[dict], None] | None = None,
    ) -> None:
        self.station_sn, self.did, self.ip, self.poort = station_sn, did, ip, poort
        self._haal_cipher = haal_cipher
        self.meting = meting or Meting()
        self.sleutel: bytes | None = None
        self._transport = None
        self._adres: tuple | None = None
        self._volgnr = 0
        self._bouwer: dict[int, Bouwer] = {}
        self._verwacht: dict[int, int] = {}
        self._laatste_pong: float | None = None
        self._ping_t: float | None = None
        self._verbonden = asyncio.Event()
        self._taken: list[asyncio.Task] = []
        self._sleutel_bezig = False
        self._bij_bericht = bij_bericht
        self._open: dict[int, tuple[bytes, int, float]] = {}  # volgnr -> (pakket, pogingen, tijd)
        self._camera_info_t = 0.0
        self._wacht_op: dict[int, asyncio.Future] = {}  # opdracht -> antwoord (returncode)
        self._laatste_vraag_t = 0.0

    def _stuur(self, data: bytes, adres) -> None:
        if self._transport is None:
            return
        self.meting.pakket("uit", data)
        self._transport.sendto(data, adres)

    # levenscyclus -------------------------------------------------------------------

    async def verbind(self) -> bool:
        lus = asyncio.get_running_loop()
        self._transport, _ = await lus.create_datagram_endpoint(
            lambda: _Udp(self), local_addr=("0.0.0.0", 0)
        )
        vraag = check_cam(self.did)
        for p in [self.poort, *range(self.poort - 3, self.poort), *range(self.poort + 1, self.poort + 4)]:
            if 0 < p < 65536:
                self._stuur(vraag, (self.ip, p))
        try:
            await asyncio.wait_for(self._verbonden.wait(), VERBIND_TIMEOUT_S)
        except asyncio.TimeoutError:
            self.meting.laatste_fout = "geen CAM_ID binnen 10 s"
            self.sluit()
            return False
        self._taken.append(lus.create_task(self._hartslag()))
        self._stuur_opdracht(CMD_GATEWAYINFO)
        return True

    def sluit(self) -> None:
        for t in self._taken:
            t.cancel()
        self._taken = []
        if self._transport is not None:
            try:
                if self._adres and self.meting.verbonden:
                    self._stuur(bericht(END), self._adres)
            except OSError:
                pass
            self._transport.close()
            self._transport = None
        if self.meting.verbonden:
            self.meting.verbonden = False

    @property
    def levend(self) -> bool:
        if not self.meting.verbonden:
            return False
        laatst = max(self._laatste_pong or 0, self.meting.laatst_iets_ontvangen or 0,
                     self.meting.verbonden_sinds or 0)
        return time.time() - laatst < MAX_ZONDER_PONG_S

    async def _hartslag(self) -> None:
        while True:
            await asyncio.sleep(HARTSLAG_S)
            if self._transport is None or self._adres is None:
                return
            self._ping_t = time.monotonic()
            self.meting.pings += 1
            self._stuur(bericht(PING), self._adres)
            if self.sleutel is not None and time.monotonic() - self._camera_info_t > CAMERA_INFO_ELKE_S:
                self.vraag_camera_info()

    def _stuur_opdracht(self, opdracht: int) -> None:
        if self._transport is None or self._adres is None:
            return
        self._verstuur_data(opdracht_zonder_data(self._volgnr, opdracht))

    def vraag_camera_info(self) -> None:
        """Stap 3: alle parameters van station en apparaten opvragen (lezen)."""
        if self._transport is None or self._adres is None:
            return
        self._camera_info_t = time.monotonic()
        self._verstuur_data(opdracht_met_getal(self._volgnr, CMD_CAMERA_INFO, KANAAL_STATION,
                                               self.sleutel, self.meting.niveau))

    async def zet_bewakingsstand(self, account_id: str, stand: int, gebruiker: str) -> int:
        """Stap 4: de bewakingsstand zetten. Geeft de returncode van het
        station (0 = gelukt); TimeoutError als het niet antwoordt."""
        if self._transport is None or self._adres is None or not self.meting.verbonden:
            raise ConnectionError("geen lokale verbinding met het station")
        if self.sleutel is None:
            raise ConnectionError("sessie nog niet versleuteld")
        tekst = bewakingsstand_tekst(account_id, stand, gebruiker)
        oud = self._wacht_op.pop(CMD_SET_PAYLOAD, None)
        if oud is not None and not oud.done():
            oud.cancel()
        # Een vraag die net weg is, kan zijn antwoord nog onderweg hebben: even wachten,
        # zodat de returncode echt bij de bewakingsstand hoort.
        rust = 1.0 - (time.monotonic() - self._laatste_vraag_t)
        if rust > 0:
            await asyncio.sleep(rust)
        wacht = asyncio.get_running_loop().create_future()
        self._wacht_op[CMD_SET_PAYLOAD] = wacht
        self._stuur_payload(tekst, "bewakingsstand")
        try:
            return await asyncio.wait_for(wacht, ANTWOORD_TIMEOUT_S)
        finally:
            self._wacht_op.pop(CMD_SET_PAYLOAD, None)

    def _stuur_payload(self, tekst: str, soort: str) -> None:
        self._verstuur_data(opdracht_met_tekst(self._volgnr, CMD_SET_PAYLOAD, tekst,
                                               self.sleutel, self.meting.niveau))
        self.meting.opdrachten_gestuurd[soort] = self.meting.opdrachten_gestuurd.get(soort, 0) + 1

    def vraag(self, tekst: str, soort: str) -> bool:
        """Stap 5: een leesvraag (database, afbeelding). Niet terwijl de
        bewakingsstand op antwoord wacht."""
        if self._transport is None or self._adres is None or self.sleutel is None:
            return False
        if CMD_SET_PAYLOAD in self._wacht_op:
            return False
        self._laatste_vraag_t = time.monotonic()
        self._stuur_payload(tekst, soort)
        return True

    def _verstuur_data(self, pakket: bytes) -> None:
        """Verstuur en houd vast tot het station het bevestigt (ACK)."""
        volgnr = self._volgnr
        self._volgnr = (self._volgnr + 1) % 0x10000
        self._open[volgnr] = (pakket, 0, time.monotonic())
        self._stuur(pakket, self._adres)
        try:
            asyncio.get_running_loop().call_later(OPNIEUW_NA_S, self._opnieuw, volgnr)
        except RuntimeError:
            pass

    def _opnieuw(self, volgnr: int) -> None:
        open_ = self._open.get(volgnr)
        if open_ is None or self._transport is None or self._adres is None:
            return
        pakket, pogingen, _ = open_
        if pogingen >= MAX_OPNIEUW:
            self._open.pop(volgnr, None)
            self.meting.laatste_fout = f"geen ACK op pakket {volgnr}"
            return
        self._open[volgnr] = (pakket, pogingen + 1, time.monotonic())
        self._stuur(pakket, self._adres)
        asyncio.get_running_loop().call_later(OPNIEUW_NA_S, self._opnieuw, volgnr)

    # ontvangen ---------------------------------------------------------------------

    def ontvang(self, data: bytes, adres) -> None:
        self.meting.pakket("in", data)
        soort = data[:2]
        if soort == CAM_ID:
            if not self.meting.verbonden:
                self._adres = adres
                self.meting.verbonden = True
                self.meting.verbonden_sinds = time.time()
                self.meting.verbindingen += 1
                self._verbonden.set()
        elif soort == PING:
            if self._transport is not None:
                self._stuur(bericht(PONG), adres)
        elif soort == PONG:
            self._laatste_pong = time.time()
            self.meting.pongs += 1
            if self._ping_t is not None:
                self.meting.rtt_ms.append(round((time.monotonic() - self._ping_t) * 1000, 1))
                del self.meting.rtt_ms[:-100]
        elif soort == ACK and len(data) >= 10 and data[5] == SOORT_DATA:
            aantal = struct.unpack(">H", data[6:8])[0]
            for i in range(min(aantal, 17)):
                if len(data) >= 10 + 2 * i:
                    self._open.pop(struct.unpack(">H", data[8 + 2 * i:10 + 2 * i])[0], None)
        elif soort == END:
            self.meting.uitval += 1
            self.meting.verbonden = False
        elif soort == DATA and len(data) >= 8:
            self._data(data, adres)

    def _data(self, data: bytes, adres) -> None:
        soort = data[5]
        volgnr = struct.unpack(">H", data[6:8])[0]
        if self._transport is not None:
            self._stuur(ack(soort, volgnr), adres)
        verwacht = self._verwacht.get(soort)
        if verwacht is not None and volgnr != verwacht:
            if (volgnr - verwacht) % 0x10000 > 0x8000:
                self.meting.data_pakket(data, "dubbel")
                return  # dubbel (al gehad)
        self._verwacht[soort] = (volgnr + 1) % 0x10000
        if soort not in (SOORT_DATA, SOORT_CONTROL):
            self.meting.data_pakket(data, f"soort {soort} overgeslagen")
            return  # video/bestanden: pas in latere stappen
        bouwer = self._bouwer.setdefault(soort, Bouwer())
        klaar = bouwer.voeg_toe(data[8:])
        self.meting.data_pakket(
            data, f"{len(klaar)} bericht(en)" if klaar else ("deel" if bouwer.kop else "geen XZYH-kop"))
        for kop, inhoud in klaar:
            self._bericht(kop, inhoud, soort)

    def _bericht(self, kop: DataKop, inhoud: bytes, soort: int = SOORT_DATA) -> None:
        resultaat = kop.soort == 1
        if kop.opdracht == CMD_GATEWAYINFO:
            # Met signCode > 0 is het antwoord versleuteld met de sleutel van
            # niveau 1 (serienummer + p2p_did); daarbinnen cipher-id en het
            # RSA-blok met de sessiesleutel (zoals eufy-security-client).
            if kop.versleuteld and inhoud and len(inhoud) % 16 == 0:
                inhoud = aes_ecb_ontsleutel(niveau1_sleutel(self.station_sn, self.did), inhoud)
            self.meting.noteer(kop, len(inhoud), True)
            if self.sleutel is None and not self._sleutel_bezig:
                self._sleutel_bezig = True
                asyncio.get_running_loop().create_task(self._zet_sleutel(gatewayinfo_kandidaten(inhoud)))
            return
        if kop.versleuteld and inhoud and len(inhoud) % 16 == 0:
            # CONTROL: altijd niveau 1 (zoals eufy-security-client); DATA: de sessiesleutel.
            sleutel = niveau1_sleutel(self.station_sn, self.did) if soort == SOORT_CONTROL else self.sleutel
            if sleutel:
                try:
                    inhoud = aes_ecb_ontsleutel(sleutel, inhoud)
                except Exception:  # noqa: BLE001
                    pass
        self.meting.noteer(kop, len(inhoud), resultaat)
        if resultaat and kop.opdracht in self._wacht_op:
            wacht = self._wacht_op[kop.opdracht]
            if not wacht.done():
                wacht.set_result(struct.unpack("<i", inhoud[:4])[0] if len(inhoud) >= 4 else -1)
        if self._bij_bericht is not None:
            from .berichten import ontleed

            try:
                self._bij_bericht(ontleed(kop.opdracht, inhoud, resultaat, kop.kanaal))
            except Exception as err:  # noqa: BLE001
                self.meting.laatste_fout = f"bericht {kop.opdracht}: {type(err).__name__}: {err}"

    async def _zet_sleutel(self, kandidaten: list[tuple[int, bytes]]) -> None:
        try:
            for cipher_id, versleuteld in kandidaten:
                if self._haal_cipher is None:
                    break
                try:
                    prive = await self._haal_cipher(cipher_id)
                except Exception as err:  # noqa: BLE001
                    self.meting.sleutel_pogingen.append(f"cipher {cipher_id}: {type(err).__name__}: {err}")
                    continue
                if not prive:
                    self.meting.sleutel_pogingen.append(f"cipher {cipher_id}: onbekend bij de cloud")
                    continue
                try:
                    sleutel = rsa_sessiesleutel(prive, versleuteld)
                except Exception as err:  # noqa: BLE001
                    self.meting.sleutel_pogingen.append(
                        f"cipher {cipher_id}: RSA {type(err).__name__}: {err} ({len(versleuteld)} bytes)")
                    continue
                self.meting.sleutel_pogingen.append(f"cipher {cipher_id}: sleutel {len(sleutel)} bytes")
                if len(sleutel) == 16:
                    self.sleutel, self.meting.niveau, self.meting.sleutel_bron = sleutel, 2, f"rsa (cipher {cipher_id})"
                    return
            self.sleutel = niveau1_sleutel(self.station_sn, self.did)
            self.meting.niveau, self.meting.sleutel_bron = 1, "serienummer"
        finally:
            del self.meting.sleutel_pogingen[:-8]
            self._sleutel_bezig = False
            if self.sleutel is not None:
                self.vraag_camera_info()

    # weergave ----------------------------------------------------------------------

    def samenvatting(self) -> dict[str, Any]:
        m = self.meting
        rtt = sorted(m.rtt_ms)
        return {
            "verbonden": m.verbonden and self.levend,
            "verbonden_sinds": m.verbonden_sinds,
            "verbindingen": m.verbindingen,
            "uitval": m.uitval,
            "pings": m.pings,
            "pongs": m.pongs,
            "rtt_mediaan_ms": rtt[len(rtt) // 2] if rtt else None,
            "versleuteling": {0: "nog niet", 1: "niveau 1 (serienummer)", 2: "niveau 2 (RSA)"}[m.niveau],
            "berichten": m.berichten,
            "per_opdracht": m.per_opdracht,
            "laatste_berichten": m.laatste[-8:],
            "laatste_fout": m.laatste_fout,
            "pakketten_in": m.ontvangen,
            "pakketten_uit": m.verzonden,
            "laatste_pakketten": m.laatste_pakketten[-6:],
            "laatste_data": m.laatste_data[-8:],
            "voorbeelden": m.voorbeelden,
            "sleutel_bron": m.sleutel_bron,
            "opdrachten_gestuurd": m.opdrachten_gestuurd,
            "sleutel_pogingen": m.sleutel_pogingen,
        }
