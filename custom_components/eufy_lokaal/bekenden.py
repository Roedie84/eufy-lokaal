"""v0.9.0: de ingeleerde personen en de nog onbekende gezichten.

Per persoon hooguit MAX_PER_PERSOON sets kenmerken (128 getallen, geen foto). Een
onbekend gezicht wacht hooguit BEWAAR_ONBEKEND_S op een naam (knop in de melding of
dienst eufy_lokaal.gezicht_inleren); de uitsnede staat dan als gezicht_<id>.jpg in de
map van het persoonslogboek. Alleen gewone bestandsfuncties: de coördinator roept ze
aan via de executor, en ze zijn zonder Home Assistant te testen."""
from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path

BESTAND = "gezichten.json"
MAX_PER_PERSOON = 20
MAX_ONBEKEND = 50
BEWAAR_ONBEKEND_S = 7 * 86400
GEZICHT_NAAM = re.compile(r"^gezicht_([0-9a-f]{12})\.jpg$")


def leeg() -> dict:
    return {"personen": {}, "onbekend": {}}


def nette_naam(naam: str) -> str | None:
    """Een naam zoals hij in melding en logboek komt; None als hij onbruikbaar is."""
    naam = re.sub(r"\s+", " ", str(naam or "")).strip()[:30]
    if not naam or naam.lower() in ("onbekend", "nee", "niemand") or not re.search(r"\w", naam):
        return None
    return naam[0].upper() + naam[1:]


def laden(map_: str | os.PathLike) -> dict:
    try:
        data = json.loads((Path(map_) / BESTAND).read_text())
    except (OSError, ValueError):
        return leeg()
    if not isinstance(data, dict):
        return leeg()
    personen = {k: [v for v in lijst if isinstance(v, list) and len(v) == 128]
                for k, lijst in (data.get("personen") or {}).items() if isinstance(lijst, list) and nette_naam(k)}
    onbekend = {k: v for k, v in (data.get("onbekend") or {}).items()
                if isinstance(v, dict) and isinstance(v.get("kenmerken"), list) and re.fullmatch(r"[0-9a-f]{12}", k)}
    return {"personen": personen, "onbekend": onbekend}


def opslaan(map_: str | os.PathLike, data: dict) -> None:
    Path(map_).mkdir(parents=True, exist_ok=True)
    doel = Path(map_) / BESTAND
    tijdelijk = doel.with_name(BESTAND + ".tmp")
    tijdelijk.write_text(json.dumps(data))
    os.replace(tijdelijk, doel)


def _weg_uitsnede(map_, gezicht_id: str) -> None:
    try:
        os.remove(Path(map_) / f"gezicht_{gezicht_id}.jpg")
    except OSError:
        pass


def onbekend_erbij(map_: str | os.PathLike, data: dict, kenmerken: list[float], uitsnede: bytes,
                   camera: str, nu: float) -> str:
    """Een onbekend gezicht bewaren tot er een naam bij komt. Geeft het id."""
    for oud_id, o in list(data["onbekend"].items()):
        if nu - float(o.get("tijd") or 0) > BEWAAR_ONBEKEND_S:
            del data["onbekend"][oud_id]
            _weg_uitsnede(map_, oud_id)
    while len(data["onbekend"]) >= MAX_ONBEKEND:
        oud_id = min(data["onbekend"], key=lambda k: data["onbekend"][k].get("tijd") or 0)
        del data["onbekend"][oud_id]
        _weg_uitsnede(map_, oud_id)
    gezicht_id = secrets.token_hex(6)
    Path(map_).mkdir(parents=True, exist_ok=True)
    (Path(map_) / f"gezicht_{gezicht_id}.jpg").write_bytes(uitsnede)
    data["onbekend"][gezicht_id] = {"kenmerken": kenmerken, "tijd": nu, "camera": camera}
    return gezicht_id


def inleren(map_: str | os.PathLike, data: dict, gezicht_id: str, naam: str | None) -> str | None:
    """Het onbekende gezicht krijgt een naam (of wordt weggegooid bij 'onbekend').
    Geeft de naam waaronder het is ingeleerd, of None."""
    o = data["onbekend"].pop(gezicht_id, None)
    _weg_uitsnede(map_, gezicht_id)
    naam = nette_naam(naam)
    if o is None or naam is None:
        return None
    # dezelfde naam in een andere schrijfwijze is dezelfde persoon
    naam = next((k for k in data["personen"] if k.lower() == naam.lower()), naam)
    lijst = data["personen"].setdefault(naam, [])
    lijst.append(o["kenmerken"])
    del lijst[:-MAX_PER_PERSOON]
    return naam


def vergeten(data: dict, naam: str) -> bool:
    for k in list(data["personen"]):
        if k.lower() == str(naam or "").strip().lower():
            del data["personen"][k]
            return True
    return False


def aantallen(data: dict) -> dict[str, int]:
    return {k: len(v) for k, v in sorted(data["personen"].items())}
