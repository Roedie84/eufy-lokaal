"""Personen herkennen op een stilstaand beeld, volledig lokaal en zonder extra pakketten.

Het model (YOLOX, Apache-2.0, Megvii) staat als gewichten plus rekenstappen in een .npz;
dit bestand rekent het uit met alleen numpy. Pillow leest de JPEG. Geen netwerk, geen cloud.
Eén beeld kost ongeveer een seconde rekentijd; altijd buiten de event-loop aanroepen."""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import numpy as np

MODELBESTAND = Path(__file__).parent / "model" / "personen.npz"
ZIJDE = 416
KLASSE_PERSOON = 0
DREMPEL = 0.45          # zekerheid vanaf waar een vak als persoon telt
NMS_OVERLAP = 0.45
MIN_HOOGTE = 0.06       # een persoon is minstens 6 % van de beeldhoogte (ruis ver weg telt niet)

_vensters = np.lib.stride_tricks.sliding_window_view


def _conv(x: np.ndarray, w: np.ndarray, b: np.ndarray | None, at: dict) -> np.ndarray:
    groep = at.get("group", 1)
    stap = at.get("strides", [1, 1])[0]
    rand = at.get("pads", [0, 0, 0, 0])[0]
    uit_k, _, kh, kw = w.shape
    if kh == 1 and groep == 1:
        if stap != 1:
            x = x[:, :, ::stap, ::stap]
        n, c, h, br = x.shape
        y = (w.reshape(uit_k, c) @ x.reshape(c, h * br)).reshape(1, uit_k, h, br)
    else:
        if rand:
            x = np.pad(x, ((0, 0), (0, 0), (rand, rand), (rand, rand)))
        v = _vensters(x[0], (kh, kw), axis=(1, 2))[:, ::stap, ::stap]  # c, h, b, kh, kw
        c, h, br = v.shape[:3]
        if groep == 1:
            # (h*b, c*kh*kw) @ (c*kh*kw, uit)
            kolommen = np.ascontiguousarray(v.transpose(1, 2, 0, 3, 4)).reshape(h * br, c * kh * kw)
            y = (kolommen @ w.reshape(uit_k, c * kh * kw).T).T.reshape(1, uit_k, h, br)
        elif groep == c == uit_k:
            y = np.einsum("chbij,cij->chb", v, w[:, 0], optimize=True)[None]
        else:
            raise ValueError(f"convolutie met groep {groep} wordt niet ondersteund")
    if b is not None:
        y = y + b.reshape(1, -1, 1, 1)
    return y


def _maxpool(x: np.ndarray, at: dict) -> np.ndarray:
    k = at["kernel_shape"][0]
    rand = at.get("pads", [0, 0, 0, 0])[0]
    if at.get("strides", [1, 1])[0] != 1:
        raise ValueError("maxpool met stap > 1 wordt niet ondersteund")
    x = np.pad(x, ((0, 0), (0, 0), (rand, rand), (rand, rand)), constant_values=-np.inf)
    x = _vensters(x, k, axis=2).max(axis=-1)
    return _vensters(x, k, axis=3).max(axis=-1)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))


class Model:
    """Leest het modelbestand één keer en rekent beelden uit."""

    def __init__(self, bestand: Path | str = MODELBESTAND) -> None:
        with np.load(bestand, allow_pickle=False) as z:
            graaf = json.loads(bytes(z["_graaf"]).decode())
            self._w = {n: (z[n].astype(np.float32) if z[n].dtype == np.float16 else z[n]) for n in z.files if n != "_graaf"}
        self._invoer, self._uitvoer, self._stappen = graaf["invoer"], graaf["uitvoer"], graaf["stappen"]
        # na welke stap een tussenresultaat niet meer nodig is (geheugen vrijgeven)
        self._laatst: dict[str, int] = {}
        for i, (_, inn, _, _) in enumerate(self._stappen):
            for n in inn:
                self._laatst[n] = i

    def reken(self, x: np.ndarray) -> np.ndarray:
        t: dict[str, np.ndarray] = {self._invoer: x.astype(np.float32, copy=False)}
        w = self._w

        def haal(n: str) -> np.ndarray:
            return w[n] if n in w else t[n]

        for i, (soort, inn, uit, at) in enumerate(self._stappen):
            if soort == "Conv":
                y = _conv(haal(inn[0]), w[inn[1]], w[inn[2]] if len(inn) > 2 else None, at)
            elif soort == "Sigmoid":
                y = _sigmoid(haal(inn[0]))
            elif soort == "Mul":
                y = haal(inn[0]) * haal(inn[1])
            elif soort == "Add":
                y = haal(inn[0]) + haal(inn[1])
            elif soort == "Concat":
                y = np.concatenate([haal(n) for n in inn], axis=at["axis"])
            elif soort == "MaxPool":
                y = _maxpool(haal(inn[0]), at)
            elif soort == "Slice":
                a = haal(inn[0])
                begin, eind, assen = (haal(n) for n in inn[1:4])
                stappen = haal(inn[4]) if len(inn) > 4 else np.ones_like(begin)
                snede = [slice(None)] * a.ndim
                for b0, e0, as_, st in zip(begin, eind, assen, stappen):
                    snede[int(as_)] = slice(int(b0), min(int(e0), a.shape[int(as_)]), int(st))
                y = a[tuple(snede)]
            elif soort == "Resize":
                a = haal(inn[0])
                schaal = haal(inn[2])
                fh, fb = int(round(float(schaal[2]))), int(round(float(schaal[3])))
                y = a.repeat(fh, axis=2).repeat(fb, axis=3)
            elif soort == "Reshape":
                a = haal(inn[0])
                vorm = [a.shape[j] if int(v) == 0 else int(v) for j, v in enumerate(haal(inn[1]))]
                y = a.reshape(vorm)
            elif soort == "Transpose":
                y = haal(inn[0]).transpose(at["perm"])
            else:
                raise ValueError(f"rekenstap {soort} wordt niet ondersteund")
            t[uit[0]] = y
            for n in inn:
                if n in t and self._laatst.get(n) == i and n != self._uitvoer:
                    del t[n]
        return t[self._uitvoer]


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
                 min_hoogte: float = MIN_HOOGTE) -> list[dict]:
    """Uit de modeluitvoer de personen: zekerheid en vak als deel van het beeld (0..1)."""
    v = _vakken(ruw)
    scores = v[:, 4] * v[:, 5 + KLASSE_PERSOON]
    # alleen waar 'persoon' ook de sterkste klasse is
    sterkste = v[:, 5:].argmax(axis=1)
    kies = (scores >= drempel) & (sterkste == KLASSE_PERSOON)
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
        _model = Model()
    beeld = Image.open(io.BytesIO(jpeg))
    x, schaal = _voorbereiden(beeld)
    alle = personen_uit(_model.reken(x)[0], schaal, beeld.width, beeld.height, ONDERGRENS)
    return {
        "personen": [p for p in alle if p["zekerheid"] >= drempel],
        "hoogste": max((p["zekerheid"] for p in alle), default=0.0),
        "breedte": beeld.width, "hoogte": beeld.height,
        "ms": round((time.perf_counter() - begin) * 1000),
    }


def teken_vakken(jpeg: bytes, personen: list[dict]) -> bytes:
    """Het beeld met om elke persoon een kader (voor de melding en het logboek)."""
    from PIL import Image, ImageDraw

    beeld = Image.open(io.BytesIO(jpeg)).convert("RGB")
    pen = ImageDraw.Draw(beeld)
    dikte = max(2, beeld.width // 320)
    for p in personen:
        x1, y1, x2, y2 = p["vak"]
        pen.rectangle((x1 * beeld.width, y1 * beeld.height, x2 * beeld.width, y2 * beeld.height),
                      outline=(255, 196, 0), width=dikte)
    uit = io.BytesIO()
    beeld.save(uit, "JPEG", quality=85)
    return uit.getvalue()
