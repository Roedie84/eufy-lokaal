"""v0.9.0: wie staat er in beeld? Gezichten zoeken en vergelijken met ingeleerde personen.

Volledig lokaal, met alleen numpy en Pillow:
- YuNet (MIT, OpenCV Zoo) zoekt gezichten en vijf punten (ogen, neus, mondhoeken);
- SFace (Apache-2.0, OpenCV Zoo) maakt van een rechtgezet gezicht 128 getallen;
- twee gezichten zijn dezelfde persoon als die getallen genoeg op elkaar lijken (cosinus).

Gezocht wordt in het bovenste deel van elk persoonsvak uit de persoonsherkenning, op het
volle beeld: zo is een gezicht dat in het hele beeld klein is toch groot genoeg.
Alles blokkeert (rekenwerk): in een werkdraad aanroepen."""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np

from .rekenen import Model

MAP = Path(__file__).parent / "model"
ZOEK_ZIJDE = 640
ZOEK_DREMPEL = 0.8       # zekerheid vanaf waar iets een gezicht is (OpenCV: 0,9 voor hele beelden)
MIN_GEZICHT_PX = 40      # v0.11.0: kleiner geeft vage kenmerken die het inleren vervuilen (was 24)
GELIJK_DREMPEL = 0.40    # cosinus vanaf waar het dezelfde persoon is (OpenCV: 0,363)
KEN_ZIJDE = 112
# waar ogen, neus en mondhoeken in een rechtgezet gezicht van 112x112 horen (ArcFace)
SJABLOON = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float64)

_zoek: Model | None = None
_ken: Model | None = None


def _modellen() -> tuple[Model, Model]:
    global _zoek, _ken
    if _zoek is None:
        _zoek = Model(MAP / "gezicht_zoek.npz")
        _ken = Model(MAP / "gezicht_ken.npz")
    return _zoek, _ken


def _nms(vakken: np.ndarray, scores: np.ndarray, overlap: float = 0.3) -> list[int]:
    from .herkenning import _nms as nms

    return nms(vakken, scores, overlap)


def zoek_gezichten(beeld, drempel: float = ZOEK_DREMPEL) -> list[dict]:
    """Gezichten in een Pillow-beeld: vak (x1,y1,x2,y2) en vijf punten in pixels, zekerheid."""
    from PIL import Image

    zoek, _ = _modellen()
    beeld = beeld.convert("RGB")
    schaal = min(ZOEK_ZIJDE / beeld.width, ZOEK_ZIJDE / beeld.height)
    nb, nh = max(1, round(beeld.width * schaal)), max(1, round(beeld.height * schaal))
    doek = np.zeros((ZOEK_ZIJDE, ZOEK_ZIJDE, 3), dtype=np.float32)
    doek[:nh, :nb] = np.asarray(beeld.resize((nb, nh), Image.Resampling.BILINEAR), dtype=np.float32)[:, :, ::-1]
    uit = zoek.reken_alle(doek.transpose(2, 0, 1)[None])
    vakken, punten, scores = [], [], []
    for stap in (8, 16, 32):
        n = ZOEK_ZIJDE // stap
        rij, kol = np.divmod(np.arange(n * n), n)
        score = np.sqrt(np.clip(uit[f"cls_{stap}"][0, :, 0], 0, 1) * np.clip(uit[f"obj_{stap}"][0, :, 0], 0, 1))
        kies = score >= drempel
        if not kies.any():
            continue
        b = uit[f"bbox_{stap}"][0][kies]
        k = uit[f"kps_{stap}"][0][kies]
        r, c = rij[kies][:, None], kol[kies][:, None]
        cx, cy = (c[:, 0] + b[:, 0]) * stap, (r[:, 0] + b[:, 1]) * stap
        w, h = np.exp(np.clip(b[:, 2], -10, 10)) * stap, np.exp(np.clip(b[:, 3], -10, 10)) * stap
        vakken.append(np.stack((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), axis=1))
        punten.append(np.stack(((c + k[:, 0::2]) * stap, (r + k[:, 1::2]) * stap), axis=2))
        scores.append(score[kies])
    if not vakken:
        return []
    vakken, punten, scores = (np.concatenate(v) / (schaal if i < 2 else 1) for i, v in
                              enumerate((vakken, punten, scores)))
    return [{"vak": [float(v) for v in vakken[i]], "punten": punten[i].tolist(), "zekerheid": round(float(scores[i]), 3)}
            for i in _nms(vakken, scores)]


def _gelijkvormig(van: np.ndarray, naar: np.ndarray) -> np.ndarray:
    """Beste draaiing + schaal + verschuiving (Umeyama) van punten 'van' naar 'naar': 2x3."""
    mv, mn = van.mean(axis=0), naar.mean(axis=0)
    v, n = van - mv, naar - mn
    cov = n.T @ v / len(van)
    u, s, vt = np.linalg.svd(cov)
    d = np.eye(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        d[1, 1] = -1
    r = u @ d @ vt
    schaal = np.trace(np.diag(s) @ d) / v.var(axis=0).sum()
    m = np.zeros((2, 3))
    m[:, :2] = schaal * r
    m[:, 2] = mn - schaal * r @ mv
    return m


def recht_zetten(beeld, punten) -> "object":
    """Het gezicht op 112x112 met ogen, neus en mond op de vaste plek (Pillow-beeld)."""
    from PIL import Image

    m = _gelijkvormig(np.asarray(punten, dtype=np.float64), SJABLOON)
    terug = np.linalg.inv(np.vstack((m, [0, 0, 1])))[:2]   # Pillow wil doel -> bron
    return beeld.convert("RGB").transform((KEN_ZIJDE, KEN_ZIJDE), Image.Transform.AFFINE,
                                          tuple(terug.reshape(-1)), Image.Resampling.BILINEAR)


def kenmerken(gezicht) -> np.ndarray:
    """128 getallen (lengte 1) van een rechtgezet gezicht."""
    _, ken = _modellen()
    x = np.asarray(gezicht.convert("RGB"), dtype=np.float32).transpose(2, 0, 1)[None]
    v = ken.reken(x)[0].astype(np.float64)
    return v / (np.linalg.norm(v) + 1e-12)


def vergelijk(v: np.ndarray, bekend: dict[str, list[list[float]]], drempel: float = GELIJK_DREMPEL) -> tuple[str | None, float]:
    """(naam, gelijkenis) van de best passende ingeleerde persoon; naam None onder de drempel."""
    beste, waarde = None, -1.0
    for naam, lijst in bekend.items():
        if not lijst:
            continue
        g = float(np.max(np.asarray(lijst, dtype=np.float64) @ v))
        if g > waarde:
            beste, waarde = naam, g
    return (beste if waarde >= drempel else None), round(waarde, 3)


def gezichten_in(jpeg: bytes, personen: list[dict], bekend: dict[str, list[list[float]]]) -> list[dict]:
    """Per persoonsvak (0..1) het gezicht: naam of None, gelijkenis, kenmerken en een uitsnede.
    Personen zonder bruikbaar gezicht (rug naar de camera, te klein) worden overgeslagen."""
    from PIL import Image

    beeld = Image.open(io.BytesIO(jpeg)).convert("RGB")
    bw, bh = beeld.size
    uit = []
    for p in personen:
        x1, y1, x2, y2 = p["vak"]
        breed, hoog = (x2 - x1) * bw, (y2 - y1) * bh
        # hoofd en schouders: bovenste helft van het vak, iets ruimer
        rand = 0.15 * breed
        kader = (max(0, round(x1 * bw - rand)), max(0, round(y1 * bh - 0.1 * hoog)),
                 min(bw, round(x2 * bw + rand)), min(bh, round(y1 * bh + 0.55 * hoog)))
        if kader[2] - kader[0] < MIN_GEZICHT_PX or kader[3] - kader[1] < MIN_GEZICHT_PX:
            continue
        stuk = beeld.crop(kader)
        gevonden = [g for g in zoek_gezichten(stuk) if g["vak"][2] - g["vak"][0] >= MIN_GEZICHT_PX]
        if not gevonden:
            continue
        g = max(gevonden, key=lambda g: (g["vak"][2] - g["vak"][0]) * g["zekerheid"])
        punten = np.asarray(g["punten"]) + np.array(kader[:2])
        recht = recht_zetten(beeld, punten)
        v = kenmerken(recht)
        naam, gelijkenis = vergelijk(v, bekend)
        buf = io.BytesIO()
        recht.save(buf, "JPEG", quality=90)
        uit.append({"naam": naam, "gelijkenis": gelijkenis, "kenmerken": [round(float(a), 5) for a in v],
                    "gezicht_px": round(g["vak"][2] - g["vak"][0]), "zekerheid": g["zekerheid"],
                    "uitsnede": buf.getvalue()})
    return uit
