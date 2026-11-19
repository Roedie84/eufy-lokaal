"""ONNX -> .npz met gewichten en rekenstappen voor custom_components/eufy_lokaal/rekenen.py.

Alleen nodig bij het vervangen van een model; Home Assistant gebruikt het niet.
Gebruik: python tools/omzetten.py model.onnx uit.npz [half]"""
import json
import sys

import numpy as np
import onnx
from onnx import numpy_helper

ATTRIBUTEN_INT = ("group", "axis", "transA", "transB")
ATTRIBUTEN_LIJST = ("pads", "strides", "kernel_shape", "perm")
ATTRIBUTEN_GETAL = ("epsilon", "alpha", "beta")


def zet_om(bron: str, doel: str, half: bool) -> None:
    g = onnx.load(bron).graph
    data, stappen = {}, []
    for t in g.initializer:
        a = numpy_helper.to_array(t)
        if a.dtype == np.float32 and half and a.size > 64:
            a = a.astype(np.float16)
        data[t.name] = a
    for n in g.node:
        if n.op_type == "Constant":
            data[n.output[0]] = numpy_helper.to_array(n.attribute[0].t)
            continue
        at = {}
        for a in n.attribute:
            if a.name in ATTRIBUTEN_INT:
                at[a.name] = a.i
            elif a.name in ATTRIBUTEN_LIJST:
                at[a.name] = list(a.ints)
            elif a.name in ATTRIBUTEN_GETAL:
                at[a.name] = a.f
        stappen.append([n.op_type, list(n.input), list(n.output), at])
    namen = sorted(data)
    kort = {n: f"w{i}" for i, n in enumerate(namen)}
    for s in stappen:
        s[1] = [kort.get(x, x) for x in s[1]]
    graaf = {"invoer": [i.name for i in g.input if i.name not in data][0],
             "uitvoer": [o.name for o in g.output], "stappen": stappen}
    np.savez_compressed(doel, _graaf=np.frombuffer(json.dumps(graaf).encode(), dtype=np.uint8),
                        **{kort[n]: data[n] for n in namen})


if __name__ == "__main__":
    zet_om(sys.argv[1], sys.argv[2], len(sys.argv) > 3 and sys.argv[3] == "half")
