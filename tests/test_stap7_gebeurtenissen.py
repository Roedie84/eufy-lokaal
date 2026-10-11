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
    assert bekend(1101) and bekend(1013) and not bekend(987654)
