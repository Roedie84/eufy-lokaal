"""v0.7.17: de laatste live momentopname per camera op schijf bewaren.

Zonder dit was de momentopname alleen in het geheugen: na elke herstart van
Home Assistant bleef *Live momentopname* onbeschikbaar tot er weer werd
aangebeld of de dienst eufy_lokaal.momentopname liep. Een herstart mag niets
kwijtraken, dus het laatste beeld staat nu in .storage/eufy_lokaal/.

Alleen gewone (synchrone) bestandsfuncties: de coordinator roept ze aan via
de executor. Zo zijn ze ook zonder Home Assistant te testen.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

MAP = ("eufy_lokaal",)
META_SLEUTELS = ("tijd", "duur_s", "reden", "grootte")


def _veilig(sn: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", sn)


def bestanden(map_: str | os.PathLike, sn: str) -> tuple[Path, Path]:
    basis = Path(map_) / f"momentopname_{_veilig(sn)}"
    return basis.with_suffix(".jpg"), basis.with_suffix(".json")


def _schrijf(pad: Path, data: bytes) -> None:
    tijdelijk = pad.with_name(pad.name + ".tmp")
    tijdelijk.write_bytes(data)
    os.replace(tijdelijk, pad)


def momentopname_opslaan(map_: str | os.PathLike, sn: str, m: dict) -> None:
    """Beeld en gegevens wegschrijven; eerst naar .tmp, dan in een keer vervangen."""
    if not m.get("data"):
        return
    Path(map_).mkdir(parents=True, exist_ok=True)
    jpg, meta = bestanden(map_, sn)
    _schrijf(jpg, m["data"])
    _schrijf(meta, json.dumps({k: m.get(k) for k in META_SLEUTELS}).encode())


def momentopname_laden(map_: str | os.PathLike, sn: str) -> dict | None:
    """Het bewaarde beeld, of None als er niets (bruikbaars) is."""
    jpg, meta = bestanden(map_, sn)
    try:
        data = jpg.read_bytes()
    except OSError:
        return None
    if not data.startswith(b"\xff\xd8"):
        return None
    try:
        gegevens = json.loads(meta.read_text())
    except (OSError, ValueError):
        gegevens = {}
    if not isinstance(gegevens, dict):
        gegevens = {}
    tijd = gegevens.get("tijd")
    if not isinstance(tijd, (int, float)):
        tijd = jpg.stat().st_mtime
    return {"data": data, "tijd": float(tijd), "duur_s": gegevens.get("duur_s"),
            "reden": gegevens.get("reden"), "grootte": len(data), "fout": None, "bewaard": True}
