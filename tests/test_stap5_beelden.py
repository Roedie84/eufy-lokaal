"""Stap 5: laatste beeld en opnames van de HomeBase."""
import base64
import json
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from custom_components.eufy_lokaal import berichten as b
from custom_components.eufy_lokaal import p2p_sessie as ps


def test_alleen_lezen_uit_de_database():
    assert ps.toegestane_payload(json.loads(ps.laatste_info_tekst("a", 1)))
    assert ps.toegestane_payload(json.loads(ps.opnames_tekst("a", ["T8113"], "20261010", "20261011", 1)))
    assert ps.toegestane_payload(json.loads(ps.afbeelding_tekst("a", "/media/x.jpg")))
    # wissen, synchroniseren of iets anders: nee
    for sub in (10000, 10001, 10002, 10003):
        assert not ps.toegestane_payload({"cmd": 1306, "payload": {"cmd": sub}})
    assert not ps.toegestane_payload({"cmd": 1308, "payload": [{"file": "a", "delete": 1}]})
    with pytest.raises(ValueError):
        ps.opdracht_met_tekst(0, ps.CMD_SET_PAYLOAD, json.dumps({"cmd": 1306, "payload": {"cmd": 10001}}))


def test_afbeeldingsleutel_als_node():
    # vectoren uit eufy-security-client (Node), eerste 16 tekens van de sleutel
    assert b._afbeelding_sleutel("T8113P1023456789", "EUPRDMB-241997-DGTKC", "XX12345678") == b"C96FDE4B1826F77A"
    assert b._afbeelding_sleutel("T8030T2325281762", "EUPRAKM-012345-ABCDE", "AB00000042") == b"6B3FDA3082815581"


def test_afbeelding_ontsleutelen():
    jpeg = b"\xff\xd8" + bytes(range(256)) * 2 + b"\xff\xd9"
    sleutel = b._afbeelding_sleutel("T8113P1023456789", "EUPRDMB-241997-DGTKC", "XX12345678")
    e = Cipher(algorithms.AES(sleutel), modes.ECB()).encryptor()
    versleuteld = e.update(jpeg[:256]) + e.finalize() + jpeg[256:]
    kop = b"eufysecurity:T8113P1023456789:XX12345678:"
    assert b.ontsleutel_afbeelding("EUPRDMB-241997-DGTKC", kop + versleuteld) == jpeg
    assert b.ontsleutel_afbeelding("x", jpeg) == jpeg


def test_database_en_afbeelding_ontleden():
    o = b.ontleed(1306, json.dumps({"cmd": 10013, "mIntRet": 0, "data": [
        {"device_sn": "T8113", "payload": {"event_count": 3, "crop_hb3_path": "/a.jpg", "crop_cloud_path": ""}}]}).encode())
    assert o["soort"] == "database" and o["sub_opdracht"] == 10013 and o["data"][0]["device_sn"] == "T8113"
    o = b.ontleed(1308, json.dumps({"file": "/a.jpg", "content": base64.b64encode(b"\xff\xd8x").decode()}).encode())
    assert o["soort"] == "afbeelding" and o["ruw"] == b"\xff\xd8x"


def test_coordinator_beelden_en_opnames():
    from custom_components.eufy_lokaal.coordinator import EufyLokaalCoordinator

    c = object.__new__(EufyLokaalCoordinator)
    c.hass, c.cloud = MagicMock(), MagicMock()
    c.stations = {"T8030": {"station_sn": "T8030", "p2p_did": "EUPRDMB-241997-DGTKC",
                            "member": {"admin_user_id": "adm"}}}
    c.apparaten = {"T8113": {"device_sn": "T8113", "station_sn": "T8030", "device_channel": 2}}
    c.laatste_info, c.beelden, c.opnames, c._beeld_voor = {}, {}, {}, {}
    c.tellingen = {"laatste_fout": None}
    sessie = MagicMock()
    sessie.vraag.return_value = True
    c.sessies = {"T8030": sessie}
    c._bij_database("T8030", b.ontleed(1306, json.dumps({"cmd": 10013, "data": [
        {"device_sn": "T8113", "payload": {"event_count": 3, "crop_hb3_path": "/a.jpg"}}]}).encode()))
    tekst, soort = sessie.vraag.call_args[0]
    assert soort == "afbeelding" and json.loads(tekst)["payload"] == [{"file": "/a.jpg"}]
    c._bij_afbeelding("T8030", {"bestand": "/a.jpg", "ruw": b"\xff\xd8abc"})
    assert c.beelden["T8113"]["data"] == b"\xff\xd8abc"
    c._bij_database("T8030", b.ontleed(1306, json.dumps({"cmd": 10017, "data": [
        {"table_name": "history_record_info", "payload": [
            {"record_id": 1, "device_sn": "T8113", "start_time": "2026-10-11 10:00:00", "storage_path": "/v1"},
            {"record_id": 2, "device_sn": "T8113", "start_time": "2026-10-11 12:00:00", "storage_path": "/v2"}]}]}).encode()))
    assert [o["record_id"] for o in c.opnames["T8113"]] == [2, 1]
