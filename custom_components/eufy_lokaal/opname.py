"""v0.13.0: een opname van de HomeBase ophalen en er gezichten (en de hond) in zoeken.

Waarom: het live beeld komt 10-15 s na de start van een opname; iemand die voorbij loopt
is dan al weg (19-11: 4 van 4 keer niemand meer in beeld). In de opname zelf loopt hij er
wel volledig in, op volle resolutie. De HomeBase stuurt de opname als dezelfde frames als
de livestream (CMD_VIDEO_FRAME), maar op het BINARY-kanaal; versleuteld met onze sleutel.

Alles hier is zuiver (geen Home Assistant) en blokkeert: in de executor aanroepen."""
from __future__ import annotations

import io
import time
from dataclasses import dataclass, field

import numpy as np

STIL_S = 6.0            # zo lang geen data na de eerste data: opname is binnen
MAX_FRAMES = 3000       # hooguit ~2 min bij 25 fps
BEELDEN_PER_S = 2.0     # zoveel beelden per seconde video beoordelen
MAX_BEELDEN = 30
ZELFDE_PERSOON = 0.5    # gezichten in één opname die zo veel lijken: dezelfde persoon


@dataclass
class Ophalen:
    """Eén lopende download (per station één tegelijk)."""
    sn: str
    kanaal: int
    pad: str
    sleutel: object
    begin: float = field(default_factory=time.time)
    frames: list = field(default_factory=list)
    bytes: int = 0
    eerste_data: float | None = None
    laatste_data: float | None = None
    klaar: bool = False
    returncode: int | None = None
    meldingen: list = field(default_factory=list)
    fout: str | None = None

    def samenvatting(self) -> dict:
        return {"pad": self.pad, "frames": len(self.frames), "bytes": self.bytes, "klaar": self.klaar,
                "returncode": self.returncode, "meldingen": self.meldingen[-6:], "fout": self.fout,
                "duur_s": round((self.laatste_data or time.time()) - self.begin, 1),
                "eerste_data_na_s": round(self.eerste_data - self.begin, 1) if self.eerste_data else None,
                "keyframes": sum(1 for f in self.frames if f.keyframe),
                "codec": self.frames[0].codec if self.frames else None,
                "maat": [self.frames[0].breedte, self.frames[0].hoogte] if self.frames else None}


def opnamepad(thumb_pad: str, opnames: list[dict]) -> str | None:
    """Het pad van de opname bij een gebeurtenisbeeld: uit de opnamelijst, anders afgeleid
    (/zx/.../20261119162456/snapshort.jpg -> /zx/.../20261119162456/20261119162456.zxvideo)."""
    for r in opnames or []:
        if r.get("thumb_path") == thumb_pad and r.get("storage_path"):
            return str(r["storage_path"])
    if not thumb_pad or "/" not in thumb_pad:
        return None
    map_ = thumb_pad.rsplit("/", 1)[0]
    naam = map_.rsplit("/", 1)[-1]
    return f"{map_}/{naam}.zxvideo" if naam.isdigit() else None


def beelden_uit(frames: list, beelden_per_s: float = BEELDEN_PER_S, max_beelden: int = MAX_BEELDEN) -> list:
    """De opname decoderen en er gelijkmatig beelden uit nemen (Pillow-beelden)."""
    import av

    if not frames:
        return []
    codec = frames[0].codec
    fps = max(1, frames[0].fps or 15)
    stap = max(1, round(fps / beelden_per_s))
    alle = []
    for c in (codec, "hevc" if codec == "h264" else "h264"):
        decoder = av.CodecContext.create(c, "r")
        alle = []
        n = 0
        for f in [*frames, None]:
            try:
                pakketten = decoder.parse(f.data if f is not None else None)
            except av.error.FFmpegError:
                continue
            for p in pakketten:
                try:
                    for beeld in decoder.decode(p):
                        if n % stap == 0:
                            alle.append(beeld.to_image())
                        n += 1
                except av.error.FFmpegError:
                    continue
        if alle:
            break
    if len(alle) > max_beelden:
        kies = np.linspace(0, len(alle) - 1, max_beelden).round().astype(int)
        alle = [alle[i] for i in kies]
    return alle


def _jpeg(beeld, kwaliteit: int = 90) -> bytes:
    uit = io.BytesIO()
    beeld.convert("RGB").save(uit, "JPEG", quality=kwaliteit)
    return uit.getvalue()


def analyseer(frames: list, bekend: dict[str, list[list[float]]]) -> dict:
    """Personen, honden en gezichten in de opname. Per persoon (gezichten die op elkaar
    lijken) het beste gezicht; de naam via de ingeleerde personen."""
    from .gezichten import gezichten_in, vergelijk
    from .herkenning import beoordeel, teken_vakken

    begin = time.perf_counter()
    beelden = beelden_uit(frames)
    gezichten: list[dict] = []
    beste = None          # (aantal personen, zekerheid, jpeg, vakken, honden)
    honden = False
    max_personen = 0
    for beeld in beelden:
        jpeg = _jpeg(beeld)
        u = beoordeel(jpeg)
        honden = honden or bool(u.get("honden"))
        if not u["personen"]:
            continue
        max_personen = max(max_personen, len(u["personen"]))
        z = max(p["zekerheid"] for p in u["personen"])
        if beste is None or (len(u["personen"]), z) > beste[:2]:
            beste = (len(u["personen"]), z, jpeg, u["personen"], u.get("honden") or [])
        for g in gezichten_in(jpeg, u["personen"], bekend):
            gezichten.append(g)
    # per persoon samenvoegen: het grootste, zekerste gezicht houden
    groepen: list[dict] = []
    for g in sorted(gezichten, key=lambda g: g["gezicht_px"] * g["zekerheid"], reverse=True):
        v = np.asarray(g["kenmerken"])
        for gr in groepen:
            if float(np.asarray(gr["kenmerken"]) @ v) >= ZELFDE_PERSOON:
                gr["aantal"] += 1
                break
        else:
            groepen.append({**g, "aantal": 1})
    for gr in groepen:
        gr["naam"], gr["gelijkenis"] = vergelijk(np.asarray(gr["kenmerken"]), bekend)
    beeld = teken_vakken(beste[2], beste[3], beste[4]) if beste else None
    return {"beelden": len(beelden), "personen": max_personen, "hond": honden, "gezichten": groepen,
            "beeld": beeld, "ms": round((time.perf_counter() - begin) * 1000)}
