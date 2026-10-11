"""Stap 6: livestream van een camera, lokaal via de HomeBase.

Verloop (zoals eufy-security-client, MIT):

1. per stream maakt de integratie een eigen RSA-sleutelpaar (1024 bit) en
   stuurt de publieke modulus mee met de startopdracht;
2. de HomeBase stuurt videoframes (CMD_VIDEO_FRAME, 1300) over het
   VIDEO-kanaal. Kop: 4 bytes lengte, 1 byte keyframe, 1 byte streamsoort,
   2 bytes volgnummer, 2 bytes fps, 2+2 bytes breedte/hoogte, 6 bytes tijd,
   dan vanaf byte 22 de data. Met signCode > 0 staat op 22..150 een met onze
   RSA-sleutel versleutelde AES-sleutel en begint de data op 151; dan zijn de
   eerste 128 bytes van de data AES-ECB versleuteld;
3. de frames (H.264 Annex B) worden per kijker in MPEG-TS verpakt (PyAV, dat
   Home Assistant zelf al gebruikt) en via een eigen HTTP-adres geserveerd.
   Dat adres is de `stream_source` van de camera-entiteit, zodat go2rtc of
   de stream-component van Home Assistant het oppakt;
4. zonder kijkers stopt de stream na STOP_ZONDER_KIJKERS_S (batterij!), en
   nooit langer dan MAX_DUUR_S achter elkaar.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import struct
import time
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Callable

from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_LOGGER = logging.getLogger(__name__)

STOP_ZONDER_KIJKERS_S = 20.0
MAX_DUUR_S = 600.0
GEEN_BEELD_TIMEOUT_S = 20.0
MAX_WACHTRIJ_FRAMES = 120


class RsaSleutel:
    """Eigen sleutelpaar voor één stream."""

    def __init__(self) -> None:
        self._sleutel = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        self._cache: dict[bytes, bytes] = {}

    def publiek_hex(self) -> str:
        """De modulus als 128 bytes hex (zoals node-rsa `n.subarray(1)`)."""
        return self._sleutel.public_key().public_numbers().n.to_bytes(128, "big").hex()

    def ontsleutel(self, blok: bytes) -> bytes:
        if blok not in self._cache:
            self._cache[blok] = self._sleutel.decrypt(blok, rsa_padding.PKCS1v15())
            if len(self._cache) > 8:
                self._cache.pop(next(iter(self._cache)))
        return self._cache[blok]

    def versleutel(self, data: bytes) -> bytes:  # voor toetsen
        return self._sleutel.public_key().encrypt(data, rsa_padding.PKCS1v15())


@dataclass
class Frame:
    data: bytes
    keyframe: bool
    breedte: int
    hoogte: int
    fps: int
    tijd: int
    soort: int


def lees_videoframe(inhoud: bytes, signcode: int, sleutel: RsaSleutel | None) -> Frame | None:
    """Eén CMD_VIDEO_FRAME-bericht naar H.264-data."""
    if len(inhoud) < 22:
        return None
    lengte = struct.unpack("<I", inhoud[0:4])[0]
    keyframe = inhoud[4] == 1
    soort = inhoud[5]
    fps, breedte, hoogte = struct.unpack("<HHH", inhoud[8:14])
    tijd = int.from_bytes(inhoud[14:20], "little")
    begin = 22
    aes = None
    if signcode > 0 and lengte >= 128:
        if sleutel is None:
            raise ValueError("versleutelde video zonder eigen RSA-sleutel")
        aes = sleutel.ontsleutel(inhoud[22:150])
        begin = 151
    ruw = inhoud[begin:begin + lengte]
    if aes is not None:
        d = Cipher(algorithms.AES(aes[:16] if len(aes) >= 16 else aes), modes.ECB()).decryptor()
        kop = ruw[:128]
        kop = kop[: len(kop) // 16 * 16]
        ruw = d.update(kop) + d.finalize() + ruw[len(kop):]
    return Frame(ruw, keyframe or _is_idr(ruw), breedte, hoogte, fps or 15, tijd, soort)


def _is_idr(data: bytes) -> bool:
    """Bevat de data een H.264 IDR (5) of SPS (7)?"""
    i = 0
    while True:
        i = data.find(b"\x00\x00\x01", i)
        if i < 0 or i + 3 >= len(data):
            return False
        if data[i + 3] & 0x1F in (5, 7):
            return True
        i += 3


class TsMuxer:
    """H.264-frames naar MPEG-TS, per kijker (begint op een keyframe)."""

    def __init__(self, schrijf: Callable[[bytes], None], breedte: int, hoogte: int, fps: int) -> None:
        import av

        class _Uit:
            def write(self, d):  # noqa: D401 - PyAV schrijft hierheen
                schrijf(bytes(d))
                return len(d)

        self._uit = _Uit()
        self._c = av.open(self._uit, "w", format="mpegts")
        self._s = self._c.add_stream("h264", rate=max(1, fps))
        self._s.width, self._s.height = breedte or 1920, hoogte or 1080
        self._s.time_base = Fraction(1, 1000)
        self._begin: float | None = None
        self._laatste = -1

    def voeg_toe(self, f: Frame) -> None:
        import av

        nu = time.monotonic()
        if self._begin is None:
            self._begin = nu
        pts = int((nu - self._begin) * 1000)
        if pts <= self._laatste:
            pts = self._laatste + 1
        self._laatste = pts
        p = av.Packet(f.data)
        p.stream = self._s
        p.time_base = Fraction(1, 1000)
        p.pts = p.dts = pts
        p.is_keyframe = f.keyframe
        self._c.mux(p)

    def sluit(self) -> None:
        try:
            self._c.close()
        except Exception:  # noqa: BLE001
            pass


@dataclass(eq=False)
class Kijker:
    wachtrij: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(MAX_WACHTRIJ_FRAMES))


class LiveStream:
    """Eén livestream van één camera; start en stopt op vraag."""

    def __init__(self, sn: str, kanaal: int, start: Callable[[str], None], stop: Callable[[], None]) -> None:
        self.sn, self.kanaal = sn, kanaal
        self._start, self._stop = start, stop
        self.sleutel: RsaSleutel | None = None
        self.kijkers: set[Kijker] = set()
        self.actief = False
        self.gestart: float | None = None
        self.laatste_frame: float | None = None
        self.frames = 0
        self.keyframes = 0
        self.fouten = 0
        self.laatste_fout: str | None = None
        self.formaat: tuple[int, int, int] | None = None
        self._stop_taak: asyncio.TimerHandle | None = None
        self.sessies = 0

    def samenvatting(self) -> dict[str, Any]:
        return {"actief": self.actief, "kijkers": len(self.kijkers), "frames": self.frames,
                "keyframes": self.keyframes, "fouten": self.fouten, "laatste_fout": self.laatste_fout,
                "formaat": self.formaat, "gestart": self.gestart, "laatste_frame": self.laatste_frame,
                "sessies": self.sessies}

    async def kijker_erbij(self) -> Kijker:
        k = Kijker()
        self.kijkers.add(k)
        if self._stop_taak is not None:
            self._stop_taak.cancel()
            self._stop_taak = None
        if not self.actief:
            self.actief = True
            # sleutel maken kost even rekentijd: niet in de event loop
            self.sleutel = await asyncio.get_running_loop().run_in_executor(None, RsaSleutel)
            self.gestart, self.laatste_frame = time.time(), None
            self.sessies += 1
            try:
                self._start(self.sleutel.publiek_hex())
            except Exception as err:  # noqa: BLE001
                self.actief = False
                self.kijkers.discard(k)
                self.laatste_fout = f"start: {err}"
                raise
        return k

    def kijker_weg(self, k: Kijker) -> None:
        self.kijkers.discard(k)
        if not self.kijkers and self.actief and self._stop_taak is None:
            self._stop_taak = asyncio.get_running_loop().call_later(STOP_ZONDER_KIJKERS_S, self.stop)

    def stop(self) -> None:
        self._stop_taak = None
        if not self.actief:
            return
        self.actief = False
        try:
            self._stop()
        except Exception as err:  # noqa: BLE001
            self.laatste_fout = f"stop: {err}"
        for k in list(self.kijkers):
            _zet(k.wachtrij, None)

    def bij_frame(self, inhoud: bytes, signcode: int) -> None:
        if not self.actief:
            return
        try:
            f = lees_videoframe(inhoud, signcode, self.sleutel)
        except Exception as err:  # noqa: BLE001
            self.fouten += 1
            self.laatste_fout = f"frame: {type(err).__name__}: {err}"
            return
        if f is None or not f.data:
            return
        self.frames += 1
        self.keyframes += f.keyframe
        self.laatste_frame = time.time()
        self.formaat = (f.breedte, f.hoogte, f.fps)
        for k in list(self.kijkers):
            _zet(k.wachtrij, f)
        if self.gestart and time.time() - self.gestart > MAX_DUUR_S:
            self.stop()


def _zet(q: asyncio.Queue, item) -> None:
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        try:
            q.get_nowait()
            q.put_nowait(item)
        except (asyncio.QueueEmpty, asyncio.QueueFull):
            pass


def nieuw_token() -> str:
    return secrets.token_urlsafe(24)


async def stuur_ts(stream: LiveStream, schrijf_async: Callable[[bytes], Any]) -> None:
    """Voor één kijker: frames uit de wachtrij als MPEG-TS wegschrijven."""
    k = await stream.kijker_erbij()
    buffer: list[bytes] = []
    muxer: TsMuxer | None = None
    begin = time.monotonic()
    try:
        while True:
            try:
                f = await asyncio.wait_for(k.wachtrij.get(), GEEN_BEELD_TIMEOUT_S)
            except asyncio.TimeoutError:
                stream.laatste_fout = "geen beeld van de HomeBase" if stream.frames == 0 else "beeld gestopt"
                return
            if f is None:
                return
            if muxer is None:
                if not f.keyframe:
                    if time.monotonic() - begin > GEEN_BEELD_TIMEOUT_S:
                        return
                    continue
                muxer = TsMuxer(buffer.append, f.breedte, f.hoogte, f.fps)
            muxer.voeg_toe(f)
            if buffer:
                data = b"".join(buffer)
                buffer.clear()
                await schrijf_async(data)
    finally:
        if muxer is not None:
            muxer.sluit()
        stream.kijker_weg(k)
