"""Stap 4: de bewakingsstand lokaal zetten."""
import asyncio
import json
import struct

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from custom_components.eufy_lokaal import p2p_sessie as ps
from custom_components.eufy_lokaal.p2p import bericht

SLEUTEL = b"5281762-241997-D"


def _ontsleutel(d):
    c = Cipher(algorithms.AES(SLEUTEL), modes.ECB()).decryptor()
    return c.update(d) + c.finalize()


def _versleutel(d):
    c = Cipher(algorithms.AES(SLEUTEL), modes.ECB()).encryptor()
    return c.update(d) + c.finalize()


def test_alleen_bewakingsstand_mag():
    with pytest.raises(ValueError):
        ps.opdracht_met_tekst(0, 1351, ps.bewakingsstand_tekst("a", 0, "x"))
    with pytest.raises(ValueError):
        ps.opdracht_met_tekst(0, ps.CMD_SET_PAYLOAD, json.dumps({"cmd": 1013, "payload": {"mode_type": 0}}))
    with pytest.raises(ValueError):
        ps.bewakingsstand_tekst("a", 7, "x")


def test_pakket_zoals_de_app():
    tekst = ps.bewakingsstand_tekst("abc", 63, "Ruud")
    pak = ps.opdracht_met_tekst(5, ps.CMD_SET_PAYLOAD, tekst, SLEUTEL, 1)
    assert pak[:2] == ps.DATA and pak[4:6] == b"\xd1\x00" and pak[6:8] == b"\x00\x05"
    kop = pak[8:24]
    assert kop[:4] == b"XZYH" and struct.unpack("<H", kop[4:6])[0] == 1350
    lengte = struct.unpack("<H", kop[6:8])[0]
    assert kop[12:14] == bytes([255, 1]) and lengte % 16 == 0
    j = json.loads(_ontsleutel(pak[24:24 + lengte]).rstrip(b"\x00"))
    assert j == {"account_id": "abc", "cmd": 1224, "mValue3": 0, "payload": {"mode_type": 63, "user_name": "Ruud"}}


def test_zet_en_wacht_op_returncode():
    async def _proef():
        s = ps.P2PSessie("T8030T2325281762", "EUPRDMB-241997-DGTKC", "1.2.3.4", 1)
        verstuurd = []

        class _T:
            def sendto(self, d, a):
                verstuurd.append(d)

            def close(self):
                pass

        s._transport, s._adres = _T(), ("1.2.3.4", 1)
        s.meting.verbonden, s.meting.niveau, s.sleutel = True, 1, SLEUTEL
        taak = asyncio.ensure_future(s.zet_bewakingsstand("abc", 0, "Ruud"))
        await asyncio.sleep(0.01)
        volgnr = struct.unpack(">H", verstuurd[0][6:8])[0]
        s.ontvang(ps.ack(0, volgnr), s._adres)
        inh = _versleutel(struct.pack("<i", 0).ljust(16, b"\x00"))
        antw = (ps.MAGIC + struct.pack("<H", 1350) + struct.pack("<I", len(inh)) + b"\x00\x00"
                + bytes([255, 1, 1, 0]) + inh)
        s.ontvang(bericht(ps.DATA, b"\xd1\x00" + struct.pack(">H", 0) + antw), s._adres)
        code = await taak
        s.sluit()
        return code, s

    code, s = asyncio.run(_proef())
    assert code == 0 and not s._open and s.meting.opdrachten_gestuurd == {"bewakingsstand": 1}


def test_zonder_verbinding_geen_opdracht():
    s = ps.P2PSessie("T8030T2325281762", "EUPRDMB-241997-DGTKC", "1.2.3.4", 1)
    with pytest.raises(ConnectionError):
        asyncio.run(s.zet_bewakingsstand("abc", 0, "Ruud"))
