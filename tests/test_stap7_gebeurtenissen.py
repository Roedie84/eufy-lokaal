"""Gebeurtenissen (aanbellen, beweging) en parameters als entiteiten."""
import json
from unittest.mock import MagicMock

from custom_components.eufy_lokaal import berichten as b
from custom_components.eufy_lokaal.gebeurtenissen import gebeurtenis_uit
from custom_components.eufy_lokaal.parameternamen import parameternaam


def _push(event_type, sn="T821451025211C7C", als_tekst=True):
    binnen = {"event_type": event_type, "device_sn": sn, "event_time": 1791726000, "msg_type": 18}
    return b.ontleed(1351, json.dumps({"cmd": 2037, "payload": json.dumps(binnen) if als_tekst else binnen}).encode())


def test_aanbellen_uit_pushbericht():
    g = gebeurtenis_uit(_push(3103))
    assert g["soort"] == "aangebeld" and g["apparaat_sn"] == "T821451025211C7C"
    assert gebeurtenis_uit(_push(3101, als_tekst=False))["soort"] == "beweging"
    assert gebeurtenis_uit(_push(9999))["soort"] == "onbekend"
    assert gebeurtenis_uit(b.ontleed(1351, b'{"cmd":6246,"payload":{"num":0}}')) is None


def test_coordinator_detecties_en_bus():
    from custom_components.eufy_lokaal.coordinator import EufyLokaalCoordinator

    c = object.__new__(EufyLokaalCoordinator)
    c.hass = MagicMock()
    c.stations = {"T8030": {"station_sn": "T8030"}}
    c.apparaten = {"T821451025211C7C": {"device_sn": "T821451025211C7C", "station_sn": "T8030", "device_channel": 0}}
    c.lokale_params, c.alarmstand, c.laatste_melding, c.berichten_per_soort = {}, {}, {}, {}
    c.detecties, c.laatste_gebeurtenis, c._bij_gebeurtenis, c.sessies = {}, {}, [], {}
    c.async_update_listeners = MagicMock()
    gezien = []
    c._bij_gebeurtenis.append(lambda sn, g: gezien.append((sn, g["soort"])))
    c._bij_bericht_voor("T8030")(_push(3103))
    assert c.detectie_aan("T821451025211C7C", "aangebeld")
    assert not c.detectie_aan("T821451025211C7C", "beweging")
    assert gezien == [("T821451025211C7C", "aangebeld")]
    namen = [a[0][0] for a in c.hass.bus.async_fire.call_args_list]
    assert "eufy_lokaal_gebeurtenis" in namen


def test_parameternamen():
    assert parameternaam(1101) == "Batterij"
    assert parameternaam(1366) == "Geluid opnemen"
    assert parameternaam(1013) == "Infrarood"
    assert parameternaam(1249) == "Clipduur"
    assert parameternaam(987654) == "Parameter 987654"


def test_alleen_bekende_parameters():
    from custom_components.eufy_lokaal.parameternamen import bekend
    assert bekend(1013) and not bekend(987654) and not bekend(1101) and not bekend(1224)


def _coord():
    from custom_components.eufy_lokaal.coordinator import EufyLokaalCoordinator

    c = object.__new__(EufyLokaalCoordinator)
    c.hass = MagicMock()
    c.stations = {"T8030": {"station_sn": "T8030"}}
    c.apparaten = {"T8416": {"device_sn": "T8416", "station_sn": "T8030", "device_channel": 2}}
    c.lokale_params, c.alarmstand, c.laatste_melding, c.berichten_per_soort = {}, {}, {}, {}
    c.detecties, c.laatste_gebeurtenis, c._bij_gebeurtenis, c.sessies = {}, {}, [], {}
    c.laatste_info, c.beelden, c.opnames, c._beeld_voor, c.tellingen = {}, {}, {}, {}, {}
    c.async_update_listeners = MagicMock()
    return c


def _info(teller, pad="/zx/a.jpg"):
    return {"soort": "database", "naam": "database", "kanaal": 255, "sub_opdracht": 10013,
            "data": [{"device_sn": "T8416", "payload": {"event_count": teller, "crop_hb3_path": pad}}]}


def test_hogere_teller_vraagt_opnames():
    c = _coord()
    bij = c._bij_bericht_voor("T8030")
    bij(_info(10))
    c.hass.loop.call_later.reset_mock()
    bij(_info(11, "/zx/b.jpg"))
    assert any(a[0][1] == c._vraag_opnames for a in c.hass.loop.call_later.call_args_list)


def test_camera_actief_vraagt_laatste_info():
    c = _coord()
    bij = c._bij_bericht_voor("T8030")
    bericht = b.ontleed(1400, bytes.fromhex("000000007f0000007d7b5a6e00000000"), kanaal=2)
    assert bericht["naam"] == "camera_actief"
    bij(bericht)
    bij(bericht)  # binnen 20 s: niet nog eens
    vertragingen = [a[0][0] for a in c.hass.loop.call_later.call_args_list]
    assert [v for v in vertragingen if v != 8] == [3, 15, 40, 75]


def test_soorten_uit_opname():
    from custom_components.eufy_lokaal.gebeurtenissen import soorten_uit_opname

    assert soorten_uit_opname(2) == ["beweging"]
    assert soorten_uit_opname(4) == []  # live kijken
    assert soorten_uit_opname(8) == ["aangebeld"]
    assert soorten_uit_opname(2, 8) == ["huisdier"]
    assert soorten_uit_opname(2, 4 | 2) == ["onbekend_persoon", "voertuig"]
    assert soorten_uit_opname(2, 2, bekend_persoon=True) == ["bekend_persoon"]
    assert soorten_uit_opname(2, 1024) == ["pakket_bezorgd"]


def _opname(rid, trigger, minuten_geleden=1):
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    t = dt_util.now() - timedelta(minutes=minuten_geleden)
    return {"record_id": rid, "device_sn": "T8416", "trigger_type": trigger,
            "start_time": t.strftime("%Y-%m-%d %H:%M:%S")}


def test_opname_geeft_persoon_en_dier_eenmalig():
    c = _coord()
    bij = c._bij_bericht_voor("T8030")
    lijst = {"soort": "database", "naam": "database", "kanaal": 255, "sub_opdracht": 10006,
             "data": [_opname(1, 2), _opname(2, 2, minuten_geleden=30), _opname(3, 4)]}
    bij(lijst)
    assert c.detectie_aan("T8416", "beweging")
    assert not c.detectie_aan("T8416", "huisdier")
    soorten = [a[0][1]["soort"] for a in c.hass.bus.async_fire.call_args_list if a[0][0] == "eufy_lokaal_gebeurtenis"]
    assert soorten == ["beweging"]  # 30 min oud telt niet; trigger 4 (live) ook niet
    bij(lijst)  # nog eens: niet opnieuw melden
    uitsneden = {"soort": "database", "naam": "database", "kanaal": 255, "sub_opdracht": 10017,
                 "data": [{"table_name": "history_record_info", "payload": [_opname(1, 2)]},
                          {"table_name": "record_crop_picture_info",
                           "payload": [{"record_id": 1, "detection_type": 8 | 2, "person_id": 0}]}]}
    bij(uitsneden)
    soorten = [a[0][1]["soort"] for a in c.hass.bus.async_fire.call_args_list if a[0][0] == "eufy_lokaal_gebeurtenis"]
    assert soorten == ["beweging", "onbekend_persoon", "huisdier"]
    assert c.detectie_aan("T8416", "huisdier") and c.detectie_aan("T8416", "persoon")


def test_uitsneden_tekst_veilig():
    import json as _j

    from custom_components.eufy_lokaal import p2p_sessie as p

    t = _j.loads(p.opnames_tekst("a", ["T8416"], "20261011", "20261012", 1, vraag=10017, vanaf="20261011213000"))
    assert t["payload"]["cmd"] == 10017 and t["payload"]["payload"]["start_time"] == "20261011213000"
    assert p.toegestane_payload(t)
    import pytest

    with pytest.raises(ValueError):
        p.opnames_tekst("a", ["T8416"], "20261011", "20261012", 1, vraag=10099)
