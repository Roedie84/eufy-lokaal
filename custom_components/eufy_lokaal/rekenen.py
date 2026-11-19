"""Een klein rekenwerk voor neurale netten, met alleen numpy (v0.8.0, uitgebreid in v0.9.0).

Een model staat als gewichten plus rekenstappen in een .npz (omgezet uit ONNX met
tools/omzetten.py). Alleen de rekenstappen die de meegeleverde modellen gebruiken
worden ondersteund; een onbekende stap geeft een duidelijke fout.
Blokkeert (rekenwerk): altijd buiten de event-loop aanroepen."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

_vensters = np.lib.stride_tricks.sliding_window_view


def _conv(x: np.ndarray, w: np.ndarray, b: np.ndarray | None, at: dict) -> np.ndarray:
    groep = at.get("group", 1)
    stap = at.get("strides", [1, 1])[0]
    rand = at.get("pads", [0, 0, 0, 0])[0]
    uit_k, per_groep, kh, kw = w.shape
    c = x.shape[1]
    if groep != 1 and not (groep == c == uit_k):
        # gewone groepen (geen dieptescheiding): per groep een gewone convolutie
        per_uit = uit_k // groep
        delen = [_conv(x[:, g * per_groep:(g + 1) * per_groep], w[g * per_uit:(g + 1) * per_uit], None,
                       {**at, "group": 1}) for g in range(groep)]
        y = np.concatenate(delen, axis=1)
    elif kh == 1 and kw == 1 and groep == 1:
        if stap != 1:
            x = x[:, :, ::stap, ::stap]
        _, c, h, br = x.shape
        y = (w.reshape(uit_k, c) @ x.reshape(c, h * br)).reshape(1, uit_k, h, br)
    else:
        if rand:
            x = np.pad(x, ((0, 0), (0, 0), (rand, rand), (rand, rand)))
        v = _vensters(x[0], (kh, kw), axis=(1, 2))[:, ::stap, ::stap]  # c, h, b, kh, kw
        c, h, br = v.shape[:3]
        if groep == 1:
            kolommen = np.ascontiguousarray(v.transpose(1, 2, 0, 3, 4)).reshape(h * br, c * kh * kw)
            y = (kolommen @ w.reshape(uit_k, c * kh * kw).T).T.reshape(1, uit_k, h, br)
        else:
            y = np.einsum("chbij,cij->chb", v, w[:, 0], optimize=True)[None]
    if b is not None:
        y = y + b.reshape(1, -1, 1, 1)
    return y


def _maxpool(x: np.ndarray, at: dict) -> np.ndarray:
    k = at["kernel_shape"][0]
    rand = at.get("pads", [0, 0, 0, 0])[0]
    stap = at.get("strides", [1, 1])[0]
    if rand:
        x = np.pad(x, ((0, 0), (0, 0), (rand, rand), (rand, rand)), constant_values=-np.inf)
    if stap == k:
        n, c, h, b = x.shape
        h2, b2 = h // k, b // k
        return x[:, :, :h2 * k, :b2 * k].reshape(n, c, h2, k, b2, k).max(axis=(3, 5))
    if stap != 1:
        raise ValueError("maxpool met deze stap wordt niet ondersteund")
    x = _vensters(x, k, axis=2).max(axis=-1)
    return _vensters(x, k, axis=3).max(axis=-1)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))


def _per_kanaal(a: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Een vector per kanaal (C,) of (C,1,1) uitlijnen op (1,C,H,B) of (1,C)."""
    a = a.reshape(-1)
    return a.reshape(1, -1, *([1] * (x.ndim - 2)))


class Model:
    """Leest het modelbestand één keer en rekent invoer uit."""

    def __init__(self, bestand: Path | str) -> None:
        with np.load(bestand, allow_pickle=False) as z:
            graaf = json.loads(bytes(z["_graaf"]).decode())
            self._w = {n: (z[n].astype(np.float32) if z[n].dtype == np.float16 else z[n])
                       for n in z.files if n != "_graaf"}
        uit = graaf["uitvoer"]
        self._uitvoer = [uit] if isinstance(uit, str) else list(uit)
        self._invoer, self._stappen = graaf["invoer"], graaf["stappen"]
        # na welke stap een tussenresultaat niet meer nodig is (geheugen vrijgeven)
        self._laatst: dict[str, int] = {}
        for i, (_, inn, _, _) in enumerate(self._stappen):
            for n in inn:
                self._laatst[n] = i

    def reken(self, x: np.ndarray) -> np.ndarray:
        """De (eerste) uitvoer."""
        return self.reken_alle(x)[self._uitvoer[0]]

    def reken_alle(self, x: np.ndarray) -> dict[str, np.ndarray]:
        t: dict[str, np.ndarray] = {self._invoer: x.astype(np.float32, copy=False)}
        w = self._w

        def haal(n: str) -> np.ndarray:
            return w[n] if n in w else t[n]

        for i, (soort, inn, uit, at) in enumerate(self._stappen):
            if soort == "Conv":
                y = _conv(haal(inn[0]), w[inn[1]], w[inn[2]] if len(inn) > 2 else None, at)
            elif soort == "Sigmoid":
                y = _sigmoid(haal(inn[0]))
            elif soort == "Relu":
                y = np.maximum(haal(inn[0]), 0)
            elif soort == "PRelu":
                a = haal(inn[0])
                y = np.where(a >= 0, a, a * _per_kanaal(haal(inn[1]), a))
            elif soort == "BatchNormalization":
                a = haal(inn[0])
                schaal, verschuif, gem, var = (_per_kanaal(haal(n), a) for n in inn[1:5])
                y = (a - gem) / np.sqrt(var + at.get("epsilon", 1e-5)) * schaal + verschuif
            elif soort == "Mul":
                y = haal(inn[0]) * haal(inn[1])
            elif soort == "Add":
                y = haal(inn[0]) + haal(inn[1])
            elif soort == "Sub":
                y = haal(inn[0]) - haal(inn[1])
            elif soort == "Dropout":
                y = haal(inn[0])
            elif soort == "Flatten":
                a = haal(inn[0])
                as_ = at.get("axis", 1)
                y = a.reshape(int(np.prod(a.shape[:as_])), -1)
            elif soort == "Gemm":
                a, b = haal(inn[0]), haal(inn[1])
                if at.get("transA"):
                    a = a.T
                if at.get("transB"):
                    b = b.T
                y = at.get("alpha", 1.0) * (a @ b)
                if len(inn) > 2:
                    y = y + at.get("beta", 1.0) * haal(inn[2])
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
                if n in t and self._laatst.get(n) == i and n not in self._uitvoer:
                    del t[n]
        return {n: t[n] for n in self._uitvoer}
