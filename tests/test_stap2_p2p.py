"""Stap 2: de lokale P2P-sessie - pakketten, opbouw van berichten, de
sessiesleutel (RSA-vector gemaakt met Node.js crypto, zoals
eufy-security-client hem gebruikt) en een volledige sessie tegen een
nagebootst station."""
import asyncio
import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from custom_components.eufy_lokaal import p2p_sessie as ps  # noqa: E402
from custom_components.eufy_lokaal.p2p import bericht, did_naar_bytes  # noqa: E402

MAP = Path(__file__).parents[1] / "custom_components" / "eufy_lokaal"
NODE = json.loads(r'''{"pem":"-----BEGIN PRIVATE KEY-----\nMIICdgIBADANBgkqhkiG9w0BAQEFAASCAmAwggJcAgEAAoGBALgKtaiecIjtgTv5\nMTuxQEwD3EEFqcTXqOwlh19+S4Xc5+zBiQMdv5dhzlJXvqnoJ2LYaf0mkREmqPIx\nu9j2K/sh37pal2aWJ3SZR2Diu1bC2sls0+XBGeMKL9BPrgdaRhQLBC0zAOgWMlG3\nA0BZNFM/nFMlMPnlqDs7ZCcrJHI7AgMBAAECgYAbY3SfUFzc88NswmviP0GbTmPD\nxWz7X0DDS952hpwEothc8nxKW238wFBtDqPQMz1RR3WWd9vU2pT/3gjfg9sNdp8c\n1PJKFEZrjETQBMAETDqQwfCYb5dQSWxuKeQVY8VwkSWvRdN0fgrlISXP3XcyKcKj\nWdAj5vI06YiYorDBwQJBAO36CC4mxNNUN/KlRw7XbhpUQbGmULrjIc6wbmSYY0t+\nTVgcFB1NLRhfcBaKOLxiAcDpHJmYGs0vJtf/hLwP/xsCQQDF+viziXZOI2Cysq6q\nz4nyp4ZTpTZQvU6WKrCg7S9PF+CQdlOho5sIwlcjt0pa1OaCAzcO9QlukIyjgSLB\nJethAkAbnIseb7kenALx0GNyQWSxAV2t8xIi3P+rL6VfXp8Gv1zSAFAFJlM5JLmo\no9j5YpW5gFKlL5TMgdAL98W63FBJAkBtyphROFIbUxM1BlJG94EQyEZ97IkKjXEf\nZd094UrzGdK3SmdmpEw9t0iLzgG2DYsnXpESAmGQpyjXI64aXuUBAkEAh253RivX\ndLCfOSwIGmCwox3BDLJJvrbuMlI+JWxaiSO4RJ5jQhlCCsib1VnX1ETzCWrS0QN9\naYntC66G1q/4Tg==\n-----END PRIVATE KEY-----\n","enc":"7368d94544719f116401507c1ba551ed308efb893fe2c31492738cf9addd7a6799b362fa56387ebaaa8d99480ef1e6c07fc723757f89f4128b25ff3db19a5fff71de84d6dfe797dae2a2ec153ecca67db504da1bb0667a2adbf4da01ffff77f4fe0ae5a5a53bdb47a5027a552082f9cb1ee42527f5d32c6e214284fb10ecd7a1"}''')
DID = "EUPRAKM-012345-ABCDE"


def test_check_cam_en_ack():
    assert ps.check_cam(DID) == b"\xf1\x41\x00\x17" + did_naar_bytes(DID) + b"\x00\x00\x00"
    assert ps.ack(0, 7) == bytes.fromhex("f1d10006d1000001 0007".replace(" ", ""))


def test_gatewayinfo_opdracht():
    pakket = ps.opdracht_zonder_data(0, ps.CMD_GATEWAYINFO)
    assert pakket[:2] == ps.DATA and pakket[4:6] == b"\xd1\x00"
    kop = ps.lees_kop(pakket[8:])
    assert kop.opdracht == 1100 and kop.kanaal == 255


def test_geen_andere_opdrachten():
    try:
        ps.opdracht_zonder_data(0, 1224)  # CMD_SET_ARMING
    except ValueError:
        return
    raise AssertionError


def test_bericht_over_meerdere_pakketten():
    inhoud = bytes(range(40))
    kop = ps.MAGIC + struct.pack("<H", 1351) + struct.pack("<I", len(inhoud)) + b"\x00\x00" + bytes([255, 0, 0, 0])
    b = ps.Bouwer()
    assert b.voeg_toe(kop + inhoud[:10]) == []
    klaar = b.voeg_toe(inhoud[10:])
    assert len(klaar) == 1 and klaar[0][0].opdracht == 1351 and klaar[0][1] == inhoud


def test_rsa_sessiesleutel_als_node():
    assert ps.rsa_ontsleutel(NODE["pem"], bytes.fromhex(NODE["enc"])) == b"0123456789abcdef"


def test_niveau1_sleutel():
    assert ps.niveau1_sleutel("T8030T2325281762", DID) == b"5281762-012345-A"


def test_gatewayinfo_uitlezen():
    rsa = bytes.fromhex(NODE["enc"])
    # HomeBase 3: cipher-id vooraan, geen returncode, opvulling achteraan.
    inhoud = struct.pack("<H", 40641) + b"\x00\x00" + rsa + bytes(12)
    k = ps.gatewayinfo_kandidaten(inhoud)
    assert k[0] == (40641, rsa + bytes(12))
    assert ps.rsa_sessiesleutel(NODE["pem"], k[0][1]) == b"0123456789abcdef"


def test_gatewayinfo_homebase3_echt_pakket():
    """Echt antwoord van de HomeBase 3 (T8030, 3.8.5.2): ontsleuteld met
    niveau 1 begint het met cipher-id 189 en twee lege bytes."""
    versleuteld = bytes.fromhex(
        "c19e9300daeae02d201a71aed44acdd9bfb63eb24bd8aac5c9f3a41a8d902683"
        "50db2f88349e0d9eb1e33a9247decf01")
    k = ps.niveau1_sleutel("T8030T2325281762", "EUPRDMB-241997-DGTKC")
    platte = ps.aes_ecb_ontsleutel(k, versleuteld)
    assert ps.gatewayinfo_uit(platte)[0] == 189 and platte[2:4] == b"\x00\x00"


def test_sessie_tegen_een_nep_station():
    async def _proef():
        class _Station(asyncio.DatagramProtocol):
            def connection_made(self, t):
                self.t = t
                self.opdrachten = []

            def datagram_received(self, data, adres):
                if data[:2] == ps.CHECK_CAM:
                    self.t.sendto(bericht(ps.CAM_ID, did_naar_bytes(DID)), adres)
                elif data[:2] == ps.PING:
                    self.t.sendto(bericht(ps.PONG), adres)
                elif data[:2] == ps.DATA:
                    volgnr = struct.unpack(">H", data[6:8])[0]
                    self.t.sendto(ps.ack(0, volgnr), adres)
                    kop = ps.lees_kop(data[8:])
                    self.opdrachten.append(kop.opdracht)
                    if kop.opdracht == ps.CMD_CAMERA_INFO:
                        self.camera_info_inhoud = data[8 + 16:]
                        self.camera_info_kop = data[8:8 + 16]
                        import json as _j
                        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
                        js = _j.dumps({"params": [{"dev_type": 255, "param_type": 1151, "param_value": "1"},
                                                  {"dev_type": 0, "param_type": 1101, "param_value": "87"}],
                                       "main_sw_version": "3.8.5.2"}).encode() + b"\x00"
                        js = js.ljust((len(js) + 15) // 16 * 16, b"\x00")
                        e = Cipher(algorithms.AES(ps.niveau1_sleutel("T8030T2325281762", DID)), modes.ECB()).encryptor()
                        inh = e.update(js) + e.finalize()
                        antw = (ps.MAGIC + struct.pack("<H", 1103) + struct.pack("<I", len(inh))
                                + b"\x00\x00" + bytes([255, 1, 0, 0]) + inh)
                        self.t.sendto(bericht(ps.DATA, b"\xd1\x02" + struct.pack(">H", 1) + antw), adres)
                        return
                    # zoals de HomeBase 3: CONTROL-kanaal, signCode 1, inhoud met
                    # niveau 1 versleuteld, geen returncode, opgevuld tot 144.
                    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
                    sleutel = bytes.fromhex(NODE["enc"])
                    plat = (struct.pack("<H", 3) + b"\x00\x00" + sleutel).ljust(144, b"\x00")
                    e = Cipher(algorithms.AES(ps.niveau1_sleutel("T8030T2325281762", DID)), modes.ECB()).encryptor()
                    inhoud = e.update(plat) + e.finalize()
                    antwoord = (ps.MAGIC + struct.pack("<H", kop.opdracht) + struct.pack("<I", len(inhoud))
                                + b"\x00\x00" + bytes([255, 1, 0, 0]) + inhoud)
                    self.t.sendto(bericht(ps.DATA, b"\xd1\x02" + struct.pack(">H", 0) + antwoord), adres)

        lus = asyncio.get_running_loop()
        t, station = await lus.create_datagram_endpoint(_Station, local_addr=("127.0.0.1", 0))
        poort = t.get_extra_info("sockname")[1]

        async def haal(cipher_id):
            assert cipher_id == 3
            return NODE["pem"]

        ontvangen = []
        s = ps.P2PSessie("T8030T2325281762", DID, "127.0.0.1", poort, haal, None, ontvangen.append)
        try:
            assert await s.verbind()
            await asyncio.sleep(0.4)
            return s, station, ontvangen
        finally:
            s.sluit()
            t.close()

    s, station, ontvangen = asyncio.run(_proef())
    assert station.opdrachten == [1100, 1103]
    assert s.meting.verbindingen == 1 and s.meting.niveau == 2 and s.sleutel == b"0123456789abcdef"
    assert s.meting.per_opdracht == {"1100": 1, "1103": 1}
    # stap 3: CMD_CAMERA_INFO gaat versleuteld (niveau 2) met 255 als getal
    assert station.camera_info_kop[12:14] == bytes([255, 2])
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    d = Cipher(algorithms.AES(b"0123456789abcdef"), modes.ECB()).decryptor()
    assert (d.update(station.camera_info_inhoud) + d.finalize())[:4] == struct.pack("<I", 255)
    info = [o for o in ontvangen if o["naam"] == "camera_info"][0]
    assert info["parameters"] == {255: {1151: "1"}, 0: {1101: "87"}}
    assert not s._open  # alles bevestigd


def test_stuurt_niets_dat_iets_verandert():
    code = (MAP / "p2p_sessie.py").read_text()
    code = re.sub(r'"""[\s\S]*?"""', "", code)
    assert "TOEGESTANE_OPDRACHTEN = frozenset({CMD_GATEWAYINFO})" in code
    assert "TOEGESTANE_OPDRACHTEN_MET_GETAL = frozenset({CMD_CAMERA_INFO})" in code
    assert code.count("self._stuur_opdracht(") == 1
    assert code.count("opdracht_met_getal(self._volgnr, CMD_CAMERA_INFO") == 1
    assert code.count("self._verstuur_data(") == 2


def test_v021_pakketten_tellen_en_meting_loopt_door():
    from custom_components.eufy_lokaal.p2p_sessie import Meting, P2PSessie

    m = Meting()
    s = P2PSessie("T8010P000", "EUPRAKM-012345-ABCDE", "192.168.2.9", 32108, None, m)
    s.ontvang(b"\xf1\x42\x00\x00", ("192.168.2.9", 32108))
    s.ontvang(b"\xf1\xe1\x00\x00", ("192.168.2.9", 32108))
    assert m.ontvangen == {"f142": 1, "f1e1": 1}
    assert m.verbindingen == 1 and m.pongs == 1
    assert s.levend
    s2 = P2PSessie("T8010P000", "EUPRAKM-012345-ABCDE", "192.168.2.9", 32108, None, m)
    assert s2.meting is m and m.ontvangen["f142"] == 1
    sv = s.samenvatting()
    assert sv["laatste_pakketten"][-1]["soort"] == "f1e1"
