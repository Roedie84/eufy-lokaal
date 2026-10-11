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

Wat deze stap NOOIT doet: een opdracht sturen die iets verandert. Alleen
CHECK_CAM, PING/PONG, ACK en CMD_GATEWAYINFO. Een broncodetoets bewaakt dat.
"""
from __future__ import annotations

import asyncio
import base64
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
CMD_PING = 1139

HARTSLAG_S = 5.0
MAX_ZONDER_PONG_S = 30.0
VERBIND_TIMEOUT_S = 10.0

TOEGESTANE_OPDRACHTEN = frozenset({CMD_GATEWAYINFO})


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
    zonder returncode vooraan: 2 bytes cipher-id (LE), 2 bytes leeg, dan de
    RSA-versleutelde sessiesleutel. Op de HomeBase 3 (firmware 3.8) is het
    antwoord 144 bytes: 4 + 128 (RSA-1024) + opvulling."""
    if len(inhoud) < 8:
        return None
    return struct.unpack("<H", inhoud[:2])[0], inhoud[4:]


def gatewayinfo_kandidaten(inhoud: bytes) -> list[tuple[int, bytes]]:
    """Zonder en met 4-byte returncode vooraan; de cloud beslist welke klopt."""
    uit = []
    for begin in (0, 4):
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

    def _stuur_opdracht(self, opdracht: int) -> None:
        if self._transport is None or self._adres is None:
            return
        self._stuur(opdracht_zonder_data(self._volgnr, opdracht), self._adres)
        self._volgnr = (self._volgnr + 1) % 0x10000

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
            self._bericht(kop, inhoud)

    def _bericht(self, kop: DataKop, inhoud: bytes) -> None:
        resultaat = kop.soort == 1
        if kop.opdracht == CMD_GATEWAYINFO:
            # Het antwoord zelf is met RSA versleuteld, niet met AES.
            self.meting.noteer(kop, len(inhoud), True)
            if self.sleutel is None and not self._sleutel_bezig:
                self._sleutel_bezig = True
                asyncio.get_running_loop().create_task(self._zet_sleutel(gatewayinfo_kandidaten(inhoud)))
            return
        if kop.versleuteld and self.sleutel and inhoud and len(inhoud) % 16 == 0:
            try:
                inhoud = aes_ecb_ontsleutel(self.sleutel, inhoud)
            except Exception:  # noqa: BLE001
                pass
        self.meting.noteer(kop, len(inhoud), resultaat)

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
                    self.meting.sleutel_pogingen.append(f"cipher {cipher_id}: RSA {type(err).__name__}")
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
            "sleutel_pogingen": m.sleutel_pogingen,
        }
