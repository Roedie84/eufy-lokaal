"""Personen herkennen op een stilstaand beeld, volledig lokaal en zonder extra pakketten.

Het model (YOLOX, Apache-2.0, Megvii) staat als gewichten plus rekenstappen in een .npz;
dit bestand rekent het uit met alleen numpy. Pillow leest de JPEG. Geen netwerk, geen cloud.
Eén beeld kost ongeveer een seconde rekentijd; altijd buiten de event-loop aanroepen."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np

MODELBESTAND = Path(__file__).parent / "model" / "personen.npz"
ZIJDE = 416
KLASSE_PERSOON = 0
KLASSE_HOND = 16        # COCO: 15 kat, 16 hond
HOND_DREMPEL = 0.45
HOND_MIN_HOOGTE = 0.03  # een hond is kleiner dan een mens
DREMPEL = 0.45          # zekerheid vanaf waar een vak als persoon telt
NMS_OVERLAP = 0.45
MIN_HOOGTE = 0.06       # een persoon is minstens 6 % van de beeldhoogte (ruis ver weg telt niet)

from .rekenen import Model, _conv, _maxpool, _sigmoid  # noqa: F401 - _conv e.d. ook voor de tests


def _voorbereiden(beeld: Any) -> tuple[np.ndarray, float]:
    """Pillow-beeld -> (1,3,416,416) BGR 0..255 met grijze rand; plus de schaal."""
    from PIL import Image

    beeld = beeld.convert("RGB")
    schaal = min(ZIJDE / beeld.width, ZIJDE / beeld.height)
    nb, nh = max(1, int(beeld.width * schaal)), max(1, int(beeld.height * schaal))
    klein = np.asarray(beeld.resize((nb, nh), Image.Resampling.BILINEAR), dtype=np.float32)
    doek = np.full((ZIJDE, ZIJDE, 3), 114.0, dtype=np.float32)
    doek[:nh, :nb] = klein[:, :, ::-1]
    return doek.transpose(2, 0, 1)[None], schaal


def _vakken(ruw: np.ndarray) -> np.ndarray:
    """Modeluitvoer (n, 85) -> midden/afmeting in pixels van het 416-beeld."""
    roosters, stappen = [], []
    for stap in (8, 16, 32):
        n = ZIJDE // stap
        ys, xs = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
        roosters.append(np.stack((xs, ys), axis=2).reshape(-1, 2))
        stappen.append(np.full((n * n, 1), stap))
    rooster, stap = np.concatenate(roosters), np.concatenate(stappen)
    uit = ruw.copy()
    uit[:, :2] = (ruw[:, :2] + rooster) * stap
    uit[:, 2:4] = np.exp(np.clip(ruw[:, 2:4], -10, 10)) * stap
    return uit


def _nms(vakken: np.ndarray, scores: np.ndarray, overlap: float) -> list[int]:
    volgorde = scores.argsort()[::-1]
    opp = (vakken[:, 2] - vakken[:, 0]) * (vakken[:, 3] - vakken[:, 1])
    houd: list[int] = []
    while volgorde.size:
        i = int(volgorde[0])
        houd.append(i)
        rest = volgorde[1:]
        x1 = np.maximum(vakken[i, 0], vakken[rest, 0]); y1 = np.maximum(vakken[i, 1], vakken[rest, 1])
        x2 = np.minimum(vakken[i, 2], vakken[rest, 2]); y2 = np.minimum(vakken[i, 3], vakken[rest, 3])
        door = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
        iou = door / (opp[i] + opp[rest] - door + 1e-9)
        volgorde = rest[iou <= overlap]
    return houd


def personen_uit(ruw: np.ndarray, schaal: float, breedte: int, hoogte: int, drempel: float = DREMPEL,
                 min_hoogte: float = MIN_HOOGTE, klasse: int = KLASSE_PERSOON) -> list[dict]:
    """Uit de modeluitvoer de personen (of, v0.10.0, een andere klasse zoals de hond):
    zekerheid en vak als deel van het beeld (0..1)."""
    v = _vakken(ruw)
    scores = v[:, 4] * v[:, 5 + klasse]
    # alleen waar deze klasse ook de sterkste is
    sterkste = v[:, 5:].argmax(axis=1)
    kies = (scores >= drempel) & (sterkste == klasse)
    v, scores = v[kies], scores[kies]
    if not len(v):
        return []
    hoeken = np.stack((v[:, 0] - v[:, 2] / 2, v[:, 1] - v[:, 3] / 2, v[:, 0] + v[:, 2] / 2, v[:, 1] + v[:, 3] / 2), axis=1) / schaal
    uit = []
    for i in _nms(hoeken, scores, NMS_OVERLAP):
        x1, y1, x2, y2 = hoeken[i]
        x1, x2 = max(0.0, x1) / breedte, min(float(breedte), x2) / breedte
        y1, y2 = max(0.0, y1) / hoogte, min(float(hoogte), y2) / hoogte
        if y2 - y1 < min_hoogte or x2 <= x1:
            continue
        uit.append({"zekerheid": round(float(scores[i]), 3), "vak": [round(float(k), 4) for k in (x1, y1, x2, y2)]})
    return uit


_model: Model | None = None


ONDERGRENS = 0.2        # vanaf hier wordt een vak onthouden (om de drempel te kunnen beoordelen)


def beoordeel(jpeg: bytes, drempel: float = DREMPEL) -> dict:
    """Eén beeld beoordelen: de personen boven de drempel, de hoogste zekerheid (ook
    onder de drempel, vanaf ONDERGRENS) en de rekentijd. Blokkeert: in een werkdraad."""
    import time

    from PIL import Image

    global _model
    begin = time.perf_counter()
    if _model is None:
        _model = Model(MODELBESTAND)
    beeld = Image.open(io.BytesIO(jpeg))
    x, schaal = _voorbereiden(beeld)
    ruw = _model.reken(x)[0]
    alle = personen_uit(ruw, schaal, beeld.width, beeld.height, ONDERGRENS)
    return {
        "personen": [p for p in alle if p["zekerheid"] >= drempel],
        # v0.10.0: honden uit hetzelfde model, zonder extra rekenwerk
        "honden": personen_uit(ruw, schaal, beeld.width, beeld.height, HOND_DREMPEL, HOND_MIN_HOOGTE, KLASSE_HOND),
        # v0.13.2: hoogste hondzekerheid, ook onder de drempel (om de drempel te kunnen bepalen)
        "hond_hoogste": max((p["zekerheid"] for p in personen_uit(ruw, schaal, beeld.width, beeld.height, ONDERGRENS,
                                                                   0.0, KLASSE_HOND)), default=0.0),
        "hoogste": max((p["zekerheid"] for p in alle), default=0.0),
        "breedte": beeld.width, "hoogte": beeld.height,
        "ms": round((time.perf_counter() - begin) * 1000),
    }


def teken_vakken(jpeg: bytes, personen: list[dict], honden: list[dict] | None = None) -> bytes:
    """Het beeld met om elke persoon een geel kader en om elke hond een blauw (melding, logboek)."""
    from PIL import Image, ImageDraw

    beeld = Image.open(io.BytesIO(jpeg)).convert("RGB")
    pen = ImageDraw.Draw(beeld)
    dikte = max(2, beeld.width // 320)
    for p in personen:
        x1, y1, x2, y2 = p["vak"]
        pen.rectangle((x1 * beeld.width, y1 * beeld.height, x2 * beeld.width, y2 * beeld.height),
                      outline=(255, 196, 0), width=dikte)
    for p in honden or []:
        x1, y1, x2, y2 = p["vak"]
        pen.rectangle((x1 * beeld.width, y1 * beeld.height, x2 * beeld.width, y2 * beeld.height),
                      outline=(0, 160, 255), width=dikte)
    uit = io.BytesIO()
    beeld.save(uit, "JPEG", quality=85)
    return uit.getvalue()
