"""Stap 3: berichten van de HomeBase leesbaar maken."""
import base64
import json
import struct

from custom_components.eufy_lokaal import berichten as b


def test_alarmstand():
    o = b.ontleed(b.CMD_GET_ALARM_MODE, bytes([63]))
    assert o["soort"] == "alarmstand" and o["alarmstand"] == "uitgeschakeld" and o["waarde"] == 63
    assert b.ontleed(b.CMD_GET_ALARM_MODE, bytes([0]))["alarmstand"] == "weg"


def test_vertraagd_alarm_en_ingeschakeld():
    assert b.ontleed(b.CMD_GET_DELAY_ALARM, struct.pack("<II", 0, 0))["soort"] == "alarm_ingeschakeld"
    o = b.ontleed(b.CMD_GET_DELAY_ALARM, struct.pack("<II", 3, 30))
    assert o["soort"] == "vertraagd_alarm" and o["gebeurtenis"] == 3 and o["vertraging_s"] == 30


def test_camera_info_met_bypass():
    info = {"params": [{"dev_type": 255, "param_type": 1224, "param_value": "1"}],
            "db_bypass_str": [{"channel": 2, "param_type": 1101,
                               "param_value": base64.b64encode(b"64").decode()}],
            "main_sw_version": "3.8.5.2"}
    o = b.ontleed(b.CMD_CAMERA_INFO, json.dumps(info).encode() + b"\x00\x00\x00")
    assert o["parameters"] == {255: {1224: "1"}, 2: {1101: "64"}} and o["firmware"] == "3.8.5.2"


def test_melding_json_en_onbekend():
    o = b.ontleed(b.CMD_NOTIFY_PAYLOAD, b'{"cmd": 1306, "payload": {"x": 1}}\x00')
    assert o["soort"] == "melding" and o["sub_opdracht"] == 1306
    o = b.ontleed(4242, b"\x01\x02")
    assert o["soort"] == "onbekend" and o["begin"] == "0102"


def test_antwoord_returncode():
    o = b.ontleed(1103, struct.pack("<i", -104), resultaat=True)
    assert o["soort"] == "antwoord" and o["returncode"] == -104


def test_coordinator_verwerkt_berichten():
    from unittest.mock import MagicMock

    from custom_components.eufy_lokaal.coordinator import EufyLokaalCoordinator

    c = object.__new__(EufyLokaalCoordinator)
    c.hass = MagicMock()
    c.stations = {"T8030": {"station_sn": "T8030"}}
    c.apparaten = {"T8113": {"device_sn": "T8113", "station_sn": "T8030", "device_channel": 2}}
    c.lokale_params, c.alarmstand, c.laatste_melding, c.berichten_per_soort = {}, {}, {}, {}
    c.async_update_listeners = MagicMock()
    bij = c._bij_bericht_voor("T8030")
    bij({"soort": "camera_info", "naam": "camera_info", "kanaal": 255,
         "parameters": {255: {1151: "1", 1224: "0"}, 2: {1101: "64"}}})
    assert c.alarmstand["T8030"]["naam"] == "thuis" and c.bewakingsstand("T8030") == "weg"
    assert c.lokale_parameters("T8113") == {1101: "64"}
    bij(b.ontleed(b.CMD_GET_ALARM_MODE, bytes([63])))
    assert c.alarmstand["T8030"]["naam"] == "uitgeschakeld"
    c.hass.bus.async_fire.assert_called_once()
    naam, data = c.hass.bus.async_fire.call_args[0]
    assert naam == "eufy_lokaal_bericht" and data["apparaat_sn"] == "T8030"
    bij({**b.ontleed(b.CMD_NOTIFY_PAYLOAD, b'{"cmd":1}'), "kanaal": 2})
    assert c.laatste_melding["T8030"]["apparaat_sn"] == "T8113"
