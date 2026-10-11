"""Stap 6: welke startopdracht bij welk cameratype (zoals eufy-security-client
`Station.startLivestream` op een HomeBase 3 met firmware >= 2.0.9.7)."""
from __future__ import annotations

import json

from .p2p_sessie import (
    CMD_DOORBELL_SET_PAYLOAD,
    CMD_SET_PAYLOAD,
    CMD_START_REALTIME_MEDIA,
    COMMAND_START_LIVESTREAM,
)

# DeviceType-nummers (eufy-security-client http/types.ts)
INDOOR_CAMERA = 30
FLOODLIGHT_8425 = 47
OUTDOOR_PT = 48            # SoloCam S340
SOLO_E30 = 88
CAMERA_S4 = 89
BATTERY_DOORBELL_E340 = 94
SOLOCAM_E42 = 98
INDOOR_PT_S350 = 104
INDOOR_PT_E30 = 105
CAMERA_4G_S330 = 111
SOLO = {32, 33, 60, 61, 62, 63, 64}

ROUTE_DEURBEL_1 = {FLOODLIGHT_8425, OUTDOOR_PT, SOLO_E30, CAMERA_S4, BATTERY_DOORBELL_E340, SOLOCAM_E42,
                   CAMERA_4G_S330, INDOOR_PT_E30, INDOOR_CAMERA}
ROUTE_S350 = {INDOOR_PT_S350}


def _json(d: dict) -> str:
    return json.dumps(d, separators=(",", ":"))


def startopdracht(device_type: int, kanaal: int, account: str, sleutel_hex: str,
                  station_sn: str) -> tuple[int, str, str]:
    """(opdracht, JSON, route) voor het starten van de livestream (H.264)."""
    if device_type in ROUTE_DEURBEL_1:
        return CMD_DOORBELL_SET_PAYLOAD, _json({
            "commandType": COMMAND_START_LIVESTREAM,
            "data": {"accountId": account, "camera_type": 0, "encryptkey": sleutel_hex,
                     "entrytype": 0, "streamtype": 0}}), "deurbel_payload_1"
    if device_type in ROUTE_S350:
        return CMD_SET_PAYLOAD, _json({
            "account_id": account, "cmd": CMD_START_REALTIME_MEDIA, "mChannel": kanaal,
            "mValue3": CMD_START_REALTIME_MEDIA,
            "payload": {"ClientOS": "Android", "accountId": account, "camera_type": 0, "entrytype": 0,
                        "key": sleutel_hex, "streamtype": 1}}), "set_payload_s350"
    if device_type in SOLO:
        return CMD_DOORBELL_SET_PAYLOAD, _json({
            "commandType": COMMAND_START_LIVESTREAM,
            "data": {"accountId": account, "encryptkey": sleutel_hex, "streamtype": 0}}), "deurbel_payload_2"
    return CMD_SET_PAYLOAD, _json({
        "account_id": account, "cmd": CMD_START_REALTIME_MEDIA, "mValue3": CMD_START_REALTIME_MEDIA,
        "payload": {"ClientOS": "Android", "key": sleutel_hex, "streamtype": 1}}), "set_payload"
