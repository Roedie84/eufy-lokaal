"""Het lokale P2P-protocol van Eufy - eerste stuk: het basisstation vinden.

Stap 1/2 van Eufy Lokaal. Het basisstation (HomeBase) luistert op UDP-poort
32108. Op een LOCAL_LOOKUP (`f1 30`, inhoud `00 00`) antwoordt het met
LOCAL_LOOKUP_RESP (`f1 41`) en zijn P2P-identiteit (p2p_did, bijvoorbeeld
`EUPRAKM-012345-ABCDE`). Komt die overeen met wat de cloud kent, dan is het
station lokaal bereikbaar en weten we op welk adres en welke poort.

Elk P2P-bericht: 2 bytes type, 2 bytes lengte (big-endian), inhoud.

Wat deze module NOOIT doet: verbinden of iets naar het station sturen
behalve de zoekvraag. De rest van het protocol (CHECK_CAM, versleutelde
sessie, opdrachten) komt in stap 2.
"""
from __future__ import annotations

import asyncio
import struct
import time
from dataclasses import dataclass

POORT = 32108
LOCAL_LOOKUP = b"\xf1\x30"
LOCAL_LOOKUP_RESP = b"\xf1\x41"


def bericht(soort: bytes, inhoud: bytes = b"") -> bytes:
    return soort + struct.pack(">H", len(inhoud)) + inhoud


def zoekvraag() -> bytes:
    return bericht(LOCAL_LOOKUP, b"\x00\x00")


def did_uit_antwoord(data: bytes) -> str | None:
    """De p2p_did uit een LOCAL_LOOKUP_RESP, of None."""
    if len(data) < 24 or data[:2] != LOCAL_LOOKUP_RESP:
        return None
    voor = data[4:12].split(b"\x00", 1)[0].decode("ascii", errors="replace")
    nummer = struct.unpack(">I", data[12:16])[0]
    achter = data[16:24].split(b"\x00", 1)[0].decode("ascii", errors="replace")
    return f"{voor}-{nummer:06d}-{achter}"


def did_naar_bytes(did: str) -> bytes:
    """Omgekeerd (voor toetsen en later CHECK_CAM): 8 + 4 + 8 bytes."""
    voor, nummer, achter = did.split("-")
    return voor.encode().ljust(8, b"\x00") + struct.pack(">I", int(nummer)) + achter.encode().ljust(8, b"\x00")


@dataclass
class Gevonden:
    did: str
    ip: str
    poort: int
    ms: float


class _Antwoorden(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.gevonden: list[tuple[bytes, tuple]] = []
        self.wachter: asyncio.Event = asyncio.Event()

    def datagram_received(self, data: bytes, adres) -> None:
        self.gevonden.append((data, adres))
        self.wachter.set()

    def error_received(self, _exc) -> None:
        pass


async def zoek(adressen: list[str], verwacht: set[str], timeout: float = 3.0) -> dict[str, Gevonden]:
    """Stuur de zoekvraag naar elk adres (een IP of een broadcastadres) en
    verzamel de antwoorden van de stations in `verwacht` (p2p_did's)."""
    lus = asyncio.get_running_loop()
    transport, proto = await lus.create_datagram_endpoint(
        _Antwoorden, local_addr=("0.0.0.0", 0), allow_broadcast=True
    )
    begin = time.monotonic()
    uit: dict[str, Gevonden] = {}
    try:
        vraag = zoekvraag()
        for adres in adressen:
            try:
                transport.sendto(vraag, (adres, POORT))
            except OSError:
                continue
        einde = begin + timeout
        while time.monotonic() < einde and len(uit) < len(verwacht):
            proto.wachter.clear()
            try:
                await asyncio.wait_for(proto.wachter.wait(), einde - time.monotonic())
            except asyncio.TimeoutError:
                break
            for data, adres in proto.gevonden:
                did = did_uit_antwoord(data)
                if did and did in verwacht and did not in uit:
                    uit[did] = Gevonden(did, adres[0], adres[1], round((time.monotonic() - begin) * 1000, 1))
            proto.gevonden.clear()
    finally:
        transport.close()
    return uit
