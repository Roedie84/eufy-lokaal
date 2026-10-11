"""Stap 3: wat de HomeBase over de lokale sessie vertelt, leesbaar gemaakt.

Zuiver (geen netwerk, geen Home Assistant): `ontleed()` krijgt het
opdrachtnummer en de ontsleutelde inhoud en geeft een woordenboek terug dat
de coördinator als gebeurtenis doorgeeft en in de sensoren verwerkt.

Nummers en vormen volgen eufy-security-client (MIT), zie NOTICE.
"""
from __future__ import annotations

import base64
import json
import struct
from typing import Any

CMD_CAMERA_INFO = 1103
CMD_GET_ALARM_MODE = 1151
CMD_GET_DEVICE_PING = 1152
CMD_GET_DELAY_ALARM = 1164
CMD_SET_ARMING = 1224
CMD_NOTIFY_PAYLOAD = 1351
CMD_DOORBELL_NOTIFY_PAYLOAD = 1701
CMD_PING = 1139
CMD_GATEWAYINFO = 1100

KANAAL_STATION = 255
KANAAL_BINNEN = 1000

PARAM_BATTERIJ = 1101
PARAM_WIFI_RSSI = 1142
PARAM_ALARMSTAND = 1151
PARAM_BEWAKINGSSTAND = 1224

ALARMSTANDEN = {
    0: "weg", 1: "thuis", 2: "schema", 3: "aangepast 1", 4: "aangepast 2",
    5: "aangepast 3", 6: "uit", 47: "geofence", 63: "uitgeschakeld",
}

NAMEN = {
    CMD_GATEWAYINFO: "gatewayinfo", CMD_CAMERA_INFO: "camera_info",
    CMD_GET_ALARM_MODE: "alarmstand", CMD_GET_DEVICE_PING: "apparaat_ping",
    CMD_GET_DELAY_ALARM: "vertraagd_alarm", CMD_NOTIFY_PAYLOAD: "melding",
    CMD_DOORBELL_NOTIFY_PAYLOAD: "deurbel_melding", CMD_PING: "ping",
}


def tekst(inhoud: bytes) -> str:
    """Tekst tot de eerste nul-byte."""
    return inhoud.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


def json_uit(inhoud: bytes) -> Any:
    t = tekst(inhoud).strip()
    if not t:
        return None
    try:
        return json.loads(t)
    except ValueError:
        return None


def alarmstand_naam(waarde: Any) -> str | None:
    try:
        return ALARMSTANDEN.get(int(waarde), f"onbekend ({int(waarde)})")
    except (TypeError, ValueError):
        return None


def parameters_uit_camera_info(info: dict) -> dict[int, dict[int, str]]:
    """{kanaal: {parameter: waarde}} uit een CMD_CAMERA_INFO-antwoord.
    `params` gebruikt dev_type als kanaal; `db_bypass_str` heeft base64-waarden."""
    uit: dict[int, dict[int, str]] = {}
    for p in info.get("params") or []:
        try:
            kanaal, soort = int(p.get("dev_type")), int(p.get("param_type"))
        except (TypeError, ValueError):
            continue
        uit.setdefault(kanaal, {})[soort] = str(p.get("param_value"))
    for p in info.get("db_bypass_str") or []:
        try:
            kanaal, soort = int(p.get("channel")), int(p.get("param_type"))
            waarde = base64.b64decode(str(p.get("param_value"))).decode("utf-8", errors="replace")
        except (TypeError, ValueError):
            continue
        uit.setdefault(kanaal, {})[soort] = waarde
    return uit


def ontleed(opdracht: int, inhoud: bytes, resultaat: bool = False, kanaal: int = KANAAL_STATION) -> dict[str, Any]:
    """Maak een binnengekomen bericht leesbaar."""
    uit: dict[str, Any] = {"opdracht": opdracht, "naam": NAMEN.get(opdracht, f"opdracht_{opdracht}"),
                           "kanaal": kanaal, "lengte": len(inhoud)}
    if resultaat:
        # Antwoord op een opdracht van ons: 4 bytes returncode, dan eventueel data.
        uit["soort"] = "antwoord"
        if len(inhoud) >= 4:
            uit["returncode"] = struct.unpack("<i", inhoud[:4])[0]
        return uit
    if opdracht == CMD_GET_ALARM_MODE and inhoud:
        uit.update(soort="alarmstand", waarde=inhoud[0], alarmstand=alarmstand_naam(inhoud[0]))
    elif opdracht == CMD_GET_DELAY_ALARM and len(inhoud) >= 8:
        gebeurtenis, vertraging = struct.unpack("<II", inhoud[:8])
        uit.update(soort="alarm_ingeschakeld" if gebeurtenis == 0 else "vertraagd_alarm",
                   gebeurtenis=gebeurtenis, vertraging_s=vertraging)
    elif opdracht == CMD_CAMERA_INFO:
        info = json_uit(inhoud)
        if isinstance(info, dict):
            uit.update(soort="camera_info", parameters=parameters_uit_camera_info(info),
                       firmware=info.get("main_sw_version"))
        else:
            uit.update(soort="onleesbaar", begin=inhoud[:48].hex())
    elif opdracht in (CMD_NOTIFY_PAYLOAD, CMD_DOORBELL_NOTIFY_PAYLOAD):
        j = json_uit(inhoud)
        uit.update(soort="melding", inhoud=j if j is not None else tekst(inhoud)[:500])
        if isinstance(j, dict) and "cmd" in j:
            uit["sub_opdracht"] = j.get("cmd")
    elif opdracht in (CMD_PING, CMD_GET_DEVICE_PING):
        uit["soort"] = "ping"
    else:
        j = json_uit(inhoud)
        if j is not None:
            uit.update(soort="json", inhoud=j)
        else:
            uit.update(soort="onbekend", begin=inhoud[:48].hex())
    return uit
