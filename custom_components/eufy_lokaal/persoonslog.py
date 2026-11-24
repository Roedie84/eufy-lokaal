"""v0.8.0: het logboek van beelden waarop een persoon is herkend.

Elk beeld staat als JPEG in .storage/eufy_lokaal/personen/; tijd, zekerheid en
aantal staan in de bestandsnaam, zodat er geen losse index is die scheef kan
lopen. Alleen gewone (synchrone) bestandsfuncties: de coördinator roept ze aan
via de executor, en ze zijn zonder Home Assistant te testen."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

SUBMAP = "personen"
BEWAAR_DAGEN = 14
MAX_PER_CAMERA = 300
VERS_S = 600            # een beeld ouder dan 10 minuten is geen nieuwe gebeurtenis

_NAAM = re.compile(r"^(?P<sn>[A-Za-z0-9-]+)_(?P<tijd>\d{9,11})_(?P<zekerheid>\d{3})_(?P<aantal>\d{1,2})\.jpg$")
_TIJD_IN_PAD = re.compile(r"/(\d{14})/")


def _veilig(sn: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "-", sn)


def bestandsnaam(sn: str, tijd: float, zekerheid: float, aantal: int) -> str:
    return f"{_veilig(sn)}_{int(tijd)}_{min(999, max(0, round(zekerheid * 100))):03d}_{min(99, max(0, aantal))}.jpg"


def ontleed(naam: str) -> dict | None:
    """Bestandsnaam -> moment, of None als het geen logboekbestand is (ook bij een pad erin)."""
    m = _NAAM.match(naam or "")
    if not m:
        return None
    return {"sn": m["sn"], "tijd": int(m["tijd"]), "zekerheid": int(m["zekerheid"]) / 100,
            "aantal": int(m["aantal"]), "bestand": naam}


def beeldtijd_uit_pad(pad: str, tijdzone) -> float | None:
    """De HomeBase zet de starttijd van de opname (lokale tijd) in de map van het beeld."""
    m = _TIJD_IN_PAD.search(pad or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=tijdzone).timestamp()
    except ValueError:
        return None


def is_vers(beeldtijd: float | None, nu: float, eerste_beeld: bool) -> bool:
    """Telt dit beeld als iets dat nu gebeurt? Het eerste beeld na een (her)start is
    vaak oud; zonder leesbare tijd telt dat beeld daarom niet."""
    if beeldtijd is None:
        return not eerste_beeld
    return abs(nu - beeldtijd) < VERS_S


def lijst(map_: str | os.PathLike, sn: str | None = None) -> list[dict]:
    """De momenten in het logboek, nieuwste eerst."""
    try:
        namen = os.listdir(map_)
    except OSError:
        return []
    uit = [m for m in (ontleed(n) for n in namen) if m and (sn is None or m["sn"] == _veilig(sn))]
    for m in uit:
        m["namen"], m["hond"] = [], False
        if m["bestand"][:-4] + ".json" in namen:
            try:
                extra = json.loads((Path(map_) / (m["bestand"][:-4] + ".json")).read_text())
                m["namen"], m["hond"] = list(extra.get("namen") or []), bool(extra.get("hond"))
            except (OSError, ValueError, AttributeError, TypeError):
                pass
    return sorted(uit, key=lambda m: m["tijd"], reverse=True)


def opruimen(map_: str | os.PathLike, nu: float) -> int:
    """Weg wat ouder is dan BEWAAR_DAGEN of buiten MAX_PER_CAMERA valt. Geeft het aantal."""
    weg, per_camera = 0, {}
    for m in lijst(map_):
        n = per_camera[m["sn"]] = per_camera.get(m["sn"], 0) + 1
        if nu - m["tijd"] > BEWAAR_DAGEN * 86400 or n > MAX_PER_CAMERA:
            try:
                os.remove(Path(map_) / m["bestand"])
                weg += 1
            except OSError:
                pass
            try:
                os.remove(Path(map_) / (m["bestand"][:-4] + ".json"))
            except OSError:
                pass
    return weg


def opslaan(map_: str | os.PathLike, sn: str, tijd: float, zekerheid: float, aantal: int, jpeg: bytes,
            nu: float | None = None, namen: list[str] | None = None, hond: bool = False) -> str:
    """Beeld wegschrijven (eerst .tmp, dan in één keer vervangen) en opruimen. Geeft de naam.
    v0.9.0: de herkende namen staan ernaast in <naam>.json."""
    Path(map_).mkdir(parents=True, exist_ok=True)
    naam = bestandsnaam(sn, tijd, zekerheid, aantal)
    doel = Path(map_) / naam
    tijdelijk = doel.with_name(naam + ".tmp")
    tijdelijk.write_bytes(jpeg)
    os.replace(tijdelijk, doel)
    if namen or hond:
        doel.with_suffix(".json").write_text(json.dumps({"namen": list(namen or []), "hond": bool(hond)}))
    opruimen(map_, tijd if nu is None else nu)
    return naam


def lees(map_: str | os.PathLike, naam: str) -> bytes | None:
    """Eén beeld uit het logboek of (v0.9.0) een gezichtsuitsnede; alleen namen die de
    integratie zelf maakt."""
    from .bekenden import GEZICHT_NAAM

    if ontleed(naam) is None and not GEZICHT_NAAM.match(naam or ""):
        return None
    try:
        return (Path(map_) / naam).read_bytes()
    except OSError:
        return None


TOKEN_BESTAND = "beeldtoken"


def beeldtoken(map_: str | os.PathLike) -> str:
    """v0.13.3: een vast token voor de beelden uit het logboek, bewaard naast de beelden.

    Het livestream-token wisselt per Home Assistant-run; een melding van gisteren of van
    voor een herstart gaf dan 404. Dit token blijft gelijk zolang het bestand bestaat."""
    import secrets

    pad = Path(map_) / TOKEN_BESTAND
    try:
        bestaand = pad.read_text().strip()
        if len(bestaand) >= 16:
            return bestaand
    except OSError:
        pass
    Path(map_).mkdir(parents=True, exist_ok=True)
    nieuw = secrets.token_urlsafe(24)
    pad.write_text(nieuw)
    return nieuw

