"""v0.1.1: de HomeBase werd niet gevonden - de cloud geeft in `ip_addr` het
publieke IP (86.88.x.x). Nu het LAN-adres uit parameter 1176 en de
broadcastadressen van Home Assistant zelf."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from custom_components.eufy_lokaal.coordinator import lan_ip, prive_ip, zoekadressen  # noqa: E402

STATION = {"ip_addr": "86.88.38.75", "params": [{"param_type": 1176, "param_value": "192.168.2.70"}]}


def test_publiek_ip_wordt_niet_gebruikt():
    assert not prive_ip("86.88.38.75") and prive_ip("192.168.2.70")
    assert lan_ip({"ip_addr": "86.88.38.75"}) is None


def test_lan_ip_uit_parameter():
    assert lan_ip(STATION) == "192.168.2.70"


def test_zoekadressen():
    adressen = zoekadressen([STATION], ["192.168.2.255"])
    assert adressen == ["192.168.2.70", "192.168.2.255", "255.255.255.255"]
    assert all(not a.startswith("86.") for a in zoekadressen([{"ip_addr": "86.88.38.75"}], []))
