"""Stap 1, zonder Home Assistant: de cloudversleuteling en het lokaal vinden.

De vectoren zijn gemaakt met Node.js crypto (createECDH("prime256v1"),
aes-256-cbc) - dezelfde bibliotheek die eufy-security-client gebruikt - zodat
de eigen Python-versie tegen de werkelijke implementatie is getoetst.
"""
import asyncio
import importlib.util
import json
import struct
import sys
from pathlib import Path

MAP = Path(__file__).parents[1] / "custom_components" / "eufy_lokaal"


def _laad(naam):
    spec = importlib.util.spec_from_file_location(f"eufy_{naam}", MAP / f"{naam}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


cloud = _laad("cloud")
p2p = _laad("p2p")
NODE = json.loads('''{"public_key":"046bf85d5fe84598b10ca6199ec09ccd7d35dcd7195fdd1dc7737a0c67006ef251b952f25eec1deed1fca191838990466357d314adfb7e9760b8d9afc8f39a95ad","secret":"a0114c1a5b770cc4ba7f1d3689868c519ccea455c07947acc0427efbcab7e836","password_enc":"zfpijdvUPBi84EKB6vzWTQ==","data_enc":"bTKV0QrevgbtNLLbyZM32rk9k5XqWYZqs1WBiP+1XqySySa2gDqw2YLswf7N5I2IaGTsvQlDGXSl40ABYQc0982jeuzFk6/DvWPTRZwJZt8=","md5_uid":"e99a18c428cb38d5f260853678922e03"}''')
PRIVE = "1f" * 32


def test_publieke_sleutel_als_node():
    assert cloud.publieke_sleutel_hex(PRIVE) == NODE["public_key"]


def test_gedeeld_geheim_als_node():
    assert cloud.gedeeld_geheim(PRIVE, cloud.SERVER_PUBLIEKE_SLEUTEL).hex() == NODE["secret"]


def test_wachtwoord_versleutelen_als_node():
    geheim = bytes.fromhex(NODE["secret"])
    assert cloud.versleutel("Wachtwoord123!", geheim) == NODE["password_enc"]


def test_antwoord_ontsleutelen():
    geheim = bytes.fromhex(NODE["secret"])
    data = json.loads(cloud.ontsleutel(NODE["data_enc"], geheim))
    assert data == [{"station_sn": "T8030P1234567890", "p2p_did": "EUPRAKM-012345-ABCDE"}]


def test_gmt_tekst():
    assert cloud.gmt_tekst(120) == "GMT+02:00" and cloud.gmt_tekst(-330) == "GMT-05:30"


def test_sessie_bewaren_zonder_wachtwoord():
    s = cloud.Sessie()
    terug = cloud.Sessie.uit(s.naar())
    assert terug.prive_sleutel == s.prive_sleutel and terug.openudid == s.openudid
    assert "wachtwoord" not in json.dumps(s.naar()).lower()
    assert not terug.geldig


def test_parameters():
    item = {"params": [{"param_type": 1101, "param_value": "87"}, {"param_type": 9, "param_value": "x"}]}
    assert cloud.parameter(item, cloud.PARAM_BATTERIJ) == 87
    assert cloud.parameter(item, 9) == "x" and cloud.parameter(item, 1) is None


def test_zoekvraag_en_antwoord():
    assert p2p.zoekvraag() == bytes.fromhex("f13000020000")
    did = "EUPRAKM-012345-ABCDE"
    antwoord = p2p.bericht(p2p.LOCAL_LOOKUP_RESP, p2p.did_naar_bytes(did))
    assert p2p.did_uit_antwoord(antwoord) == did
    assert p2p.did_uit_antwoord(b"\xf1\x41\x00") is None
    assert p2p.did_uit_antwoord(b"\xf1\x42" + antwoord[2:]) is None


def test_zoeken_tegen_een_nep_station(monkeypatch):
    did = "EUPRAKM-012345-ABCDE"

    class _Station(asyncio.DatagramProtocol):
        def connection_made(self, t):
            self.t = t

        def datagram_received(self, data, adres):
            if data == p2p.zoekvraag():
                self.t.sendto(p2p.bericht(p2p.LOCAL_LOOKUP_RESP, p2p.did_naar_bytes(did)), adres)

    async def _proef():
        lus = asyncio.get_running_loop()
        t, _ = await lus.create_datagram_endpoint(_Station, local_addr=("127.0.0.1", 0))
        monkeypatch.setattr(p2p, "POORT", t.get_extra_info("sockname")[1])
        try:
            return await p2p.zoek(["127.0.0.1"], {did, "ANDER-000001-XYZ"}, timeout=0.5)
        finally:
            t.close()

    gevonden = asyncio.run(_proef())
    assert list(gevonden) == [did] and gevonden[did].ip == "127.0.0.1"


def test_alleen_zoeken_niets_sturen():
    """Stap 1 stuurt het station niets anders dan de zoekvraag."""
    code = (MAP / "p2p.py").read_text()
    assert code.count("transport.sendto(") == 1
