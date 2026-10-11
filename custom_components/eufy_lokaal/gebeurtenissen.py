"""Gebeurtenissen die de HomeBase 3 lokaal meldt (CMD_NOTIFY_PAYLOAD met
cmd 2037 = CMD_CAMERA_PUSH_NOTIFY; daarin dezelfde JSON als een pushbericht).
Nummers volgen eufy-security-client (HB3PairedDevicePushEvent, DoorbellPushEvent)."""
from __future__ import annotations

import json
from typing import Any

CMD_CAMERA_PUSH_NOTIFY = 2037

SOORTEN = {
    3101: "beweging", 3102: "gezicht", 3103: "aangebeld", 3104: "huilen", 3105: "geluid",
    3106: "huisdier", 3107: "voertuig", 3108: "hond", 3109: "hond", 3110: "hond",
    3111: "bekend_persoon", 3112: "onbekend_persoon",
    3301: "pakket_bezorgd", 3302: "pakket_opgehaald", 3303: "familie", 3304: "pakket_blijft_liggen",
    3305: "rondhangen", 3306: "beweging", 3307: "weg_van_huis", 3308: "beweging",
}

# binaire sensoren: naam -> welke soorten hem aanzetten
DETECTIES = {
    "beweging": {"beweging", "gezicht", "bekend_persoon", "onbekend_persoon", "huisdier", "hond", "voertuig",
                 "rondhangen", "familie"},
    "persoon": {"gezicht", "bekend_persoon", "onbekend_persoon", "familie", "rondhangen"},
    "aangebeld": {"aangebeld"},
    "huisdier": {"huisdier", "hond"},
    "voertuig": {"voertuig"},
    "geluid": {"geluid", "huilen"},
    "pakket": {"pakket_bezorgd", "pakket_blijft_liggen"},
}
DUUR_S = 30
GEBEURTENISSOORTEN = sorted(set(SOORTEN.values()) | {"onbekend"})


def gebeurtenis_uit(melding: dict) -> dict[str, Any] | None:
    """Uit een ontlede CMD_NOTIFY_PAYLOAD (soort "melding") de gebeurtenis, of None."""
    j = melding.get("inhoud")
    if not isinstance(j, dict) or j.get("cmd") != CMD_CAMERA_PUSH_NOTIFY:
        return None
    binnen = j.get("payload")
    if isinstance(binnen, str):
        try:
            binnen = json.loads(binnen)
        except ValueError:
            return None
    if not isinstance(binnen, dict):
        return None
    try:
        nummer = int(binnen.get("event_type"))
    except (TypeError, ValueError):
        nummer = None
    soort = SOORTEN.get(nummer, "onbekend")
    return {
        "soort": soort, "event_type": nummer, "apparaat_sn": binnen.get("device_sn"),
        "persoon": binnen.get("person_name") or None, "tijd_station": binnen.get("event_time"),
        "kanaal": binnen.get("channel"), "ruw": {k: v for k, v in binnen.items() if k not in ("pic_url",)},
    }


# v0.7.12: soorten uit de database van de HomeBase (eufy-security-client: TriggerType, FilterDetectType)
# 4 heet in eufy-security-client PERSON, maar blijkt bij de HomeBase 3 een opname door live kijken
# (11-10: elke live-weergave in app of HA gaf trigger 4, zonder persoon in beeld).
TRIGGER_LIVE, TRIGGER_BEL = 4, 8
DETECTIE_BITS = [
    (1, "onbekend_persoon"), (2, "onbekend_persoon"), (4, "voertuig"), (8, "huisdier"), (128, "geluid"),
    (256, "huilen"), (512, "pakket_opgehaald"), (1024, "pakket_bezorgd"), (2048, "pakket_bezorgd"),
]


def soorten_uit_opname(trigger_type, detectie_bits: int = 0, bekend_persoon: bool = False) -> list[str]:
    """Welke gebeurtenissen bij een opname horen (zonder dubbelen, in vaste volgorde)."""
    uit: list[str] = []
    try:
        t = int(trigger_type or 0)
    except (TypeError, ValueError):
        t = 0
    if t & TRIGGER_LIVE and not t & ~TRIGGER_LIVE and not detectie_bits:
        return []  # opname door live kijken (app of HA), geen gebeurtenis
    if t & TRIGGER_BEL:
        uit.append("aangebeld")
    for bit, soort in DETECTIE_BITS:
        if detectie_bits & bit and soort not in uit:
            uit.append(soort)
    if bekend_persoon and "onbekend_persoon" in uit:
        uit[uit.index("onbekend_persoon")] = "bekend_persoon"
    if not uit:
        uit.append("beweging")
    return uit
