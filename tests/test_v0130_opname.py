"""v0.13.0: een opname van de HomeBase ophalen en er gezichten in zoeken."""
import asyncio
import io
import json
import time
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
from PIL import Image

from custom_components.eufy_lokaal import opname as op
from custom_components.eufy_lokaal import p2p_sessie as ps
from custom_components.eufy_lokaal import video as v

from test_v090_gezichten import BEELD, STATION, TUIN, _coordinator, _pad


def _opname(beeld_jpeg: bytes, aantal: int = 20, fps: int = 10) -> list[v.Frame]:
    """Een 'opname' als H.264-frames, zoals lees_videoframe ze teruggeeft."""
    import av

    im = Image.open(io.BytesIO(beeld_jpeg)).convert("RGB")
    enc = av.CodecContext.create("libx264", "w")
    enc.width, enc.height, enc.pix_fmt = im.width, im.height, "yuv420p"
    enc.time_base = __import__("fractions").Fraction(1, fps)
    enc.options = {"bf": "0", "g": "10", "annexb": "1"}
    frames = []
    for i in range(aantal):
        f = av.VideoFrame.from_image(im)
        f.pts = i
        for p in enc.encode(f):
            frames.append(v.Frame(bytes(p), p.is_keyframe, im.width, im.height, fps, i, 1))
    for p in enc.encode(None):
        frames.append(v.Frame(bytes(p), p.is_keyframe, im.width, im.height, fps, 0, 1))
    return frames


# opdracht --------------------------------------------------------------------------

def test_download_opdracht_toegestaan_en_versleutelbaar():
    t = ps.download_tekst("acc", 3, "/zx/hdd_data0/Camera03/202611/20261119162456/20261119162456.zxvideo", "ab" * 128)
    j = json.loads(t)
    assert j["cmd"] == 1024 and j["mChannel"] == 3 and j["payload"]["key"] == "AB" * 128
    assert ps.toegestane_payload(j)
    pakket = ps.opdracht_met_tekst(1, ps.CMD_SET_PAYLOAD, t, b"0123456789abcdef", 2, 3)
    assert pakket[:2] == ps.DATA and ps.MAGIC in pakket


@pytest.mark.parametrize("pad", ["../etc/passwd", "/zx/../../etc/passwd", "/tmp/x", 5])
def test_alleen_paden_op_de_homebase(pad):
    j = {"cmd": 1024, "payload": {"filepath": pad, "key": "AB"}}
    assert not ps.toegestane_payload(j)
    assert not ps.toegestane_payload({"cmd": 1024, "payload": {"filepath": "/zx/a", "key": "AB", "delete": 1}})


def test_broncode_stuurt_nog_steeds_alleen_lezende_opdrachten():
    from pathlib import Path

    code = (Path(ps.__file__)).read_text()
    assert "TOEGESTANE_OPDRACHTEN = frozenset({CMD_GATEWAYINFO})" in code
    # geen wis- of schrijfopdrachten voor opnames
    for verboden in ("CMD_DELETE", "delete_record", "CMD_DOWNLOAD_CANCEL"):
        assert verboden not in code


def test_binaire_frames_gaan_naar_de_opname():
    s = ps.P2PSessie("T8030", "EUPRD-1-X", "1.2.3.4", 32108, None, None, None)
    gezien = []
    s._bij_binair = lambda kop, inhoud: gezien.append((kop.opdracht, len(inhoud)))
    inhoud = b"\x01" * 40
    kop = ps.MAGIC + __import__("struct").pack("<HI", ps.CMD_VIDEO_FRAME, len(inhoud)) + b"\x00\x00\x03\x00\x01\x00"
    pakket = ps.DATA + b"\x00\x00\xd1" + bytes([ps.SOORT_BINARY]) + b"\x00\x00" + kop + inhoud
    s._transport = None
    s._data(pakket, ("1.2.3.4", 1))
    assert gezien == [(ps.CMD_VIDEO_FRAME, 40)] and s.meting.binair == 1


# pad en analyse --------------------------------------------------------------------

def test_opnamepad():
    thumb = "/zx/hdd_data0/Camera03/202611/20261119162456/snapshort.jpg"
    assert op.opnamepad(thumb, []) == "/zx/hdd_data0/Camera03/202611/20261119162456/20261119162456.zxvideo"
    assert op.opnamepad(thumb, [{"thumb_path": thumb, "storage_path": "/zx/x.zxvideo"}]) == "/zx/x.zxvideo"
    assert op.opnamepad("", []) is None and op.opnamepad("/zx/geen_getal/s.jpg", []) is None


def test_beelden_uit_een_opname():
    frames = _opname(BEELD, aantal=20, fps=10)
    beelden = op.beelden_uit(frames, beelden_per_s=2)
    assert 3 <= len(beelden) <= 5 and beelden[0].size == (1280, 720)
    assert len(op.beelden_uit(frames, beelden_per_s=10, max_beelden=6)) == 6
    assert op.beelden_uit([]) == []


def test_analyse_houdt_alleen_het_bruikbare_gezicht_een_keer():
    from custom_components.eufy_lokaal import gezichten as gz

    # in het testbeeld kijkt de linker persoon opzij (frontaal 0): die telt niet als gezicht
    frames = _opname(BEELD, aantal=12, fps=6)
    a = op.analyseer(frames, {})
    assert a["personen"] == 2 and a["beelden"] >= 3 and not a["hond"]
    assert a["gezichten_gezien"] >= 2 * a["beelden"] - 1 and a["gezichten_bruikbaar"] >= a["beelden"] - 1
    assert len(a["gezichten"]) == 1 and a["gezichten"][0]["aantal"] >= 3
    assert a["gezichten"][0]["frontaal"] >= op.MIN_FRONTAAL and a["beeld"][:2] == b"\xff\xd8"
    # ingeleerd: herkend
    b = op.analyseer(frames, {"Ruud": [a["gezichten"][0]["kenmerken"]]})
    assert [g["naam"] for g in b["gezichten"]] == ["Ruud"]
    assert gz.MIN_GEZICHT_PX <= a["gezichten"][0]["gezicht_px"]


# coördinator -----------------------------------------------------------------------

class _NepSessie:
    """Stuurt na start_download een paar frames en daarna CMD_DOWNLOAD_FINISH."""

    def __init__(self, c, frames, code=0):
        self.c, self.frames, self.code, self.levend, self.teksten = c, frames, code, True, []

    def start_download(self, kanaal, tekst):
        self.teksten.append(json.loads(tekst))
        loop = asyncio.get_running_loop()
        bij_bericht = self.c._bij_bericht_voor(STATION)
        loop.call_soon(bij_bericht, {"soort": "antwoord", "opdracht": 1350, "returncode": self.code})
        if self.code:
            return
        sleutel = self.c._ophalen[STATION].sleutel
        bij = self.c._bij_binair_voor(STATION)
        for i, f in enumerate(self.frames):
            aes = b"S" * 16
            from test_stap6_live import _videobericht
            inhoud = _videobericht(f.data, sleutel, aes)
            loop.call_later(0.01 * i, bij, NS(opdracht=ps.CMD_VIDEO_FRAME, kanaal=3, versleuteld=1), inhoud)
        loop.call_later(0.01 * len(self.frames) + 0.05, bij_bericht, {"soort": "onbekend", "opdracht": 1304})


def _met_sessie(tmp_path, frames, code=0):
    c = _coordinator(tmp_path, live=None)
    c.apparaten[TUIN]["device_channel"] = 3
    c.cloud = NS(sessie=NS(user_id="u"))
    c.lokale_params, c.alarmstand, c.laatste_melding, c.berichten_per_soort = {}, {}, {}, {}
    c.opnames = {}
    c.sessies = {STATION: _NepSessie(c, frames, code)}
    return c


def test_opname_ophalen_en_bekijken(tmp_path):
    frames = _opname(BEELD, aantal=12, fps=6)
    c = _met_sessie(tmp_path, frames)
    pad = "/zx/hdd_data0/Camera03/202611/20261119162456/20261119162456.zxvideo"
    uit = asyncio.run(c.async_opname_bekijken(TUIN, pad))
    o = uit["ophalen"]
    assert o["frames"] == len(frames) and o["returncode"] == 0 and o["klaar"] and o["fout"] is None
    assert any(m["opdracht"] == 1304 for m in o["meldingen"])
    assert c.sessies[STATION].teksten[0]["payload"]["filepath"] == pad
    assert len(uit["analyse"]["gezichten"]) == 1
    assert c.tellingen["opnames_opgehaald"]["gelukt"] == 1


def test_homebase_weigert(tmp_path):
    c = _met_sessie(tmp_path, [], code=-104)
    uit = asyncio.run(c.async_opname_bekijken(TUIN, "/zx/a/1/1.zxvideo"))
    assert "-104" in uit["ophalen"]["fout"] and "analyse" not in uit


def test_namen_nagekomen_uit_de_opname(tmp_path):
    frames = _opname(BEELD, aantal=12, fps=6)
    c = _met_sessie(tmp_path, frames)
    moment = {"tijd": int(time.time()) - 20, "zekerheid": 0.8, "aantal": 2, "bestand": None, "namen": [],
              "hond": False}
    c.personen[TUIN] = [moment]
    asyncio.run(c._async_namen_uit_opname(STATION, TUIN, _pad(time.time() - 20), moment))
    data = c.hass.bus.async_fire.call_args[0][1]
    assert data["soort"] == "namen" and data["bron"] == "opname" and data["onbekende_gezichten"] == 1
    assert data["gezicht_id"] in c.bekenden["onbekend"] and data["moment"] == moment["tijd"]
    assert moment["bestand"] and c.persoonsbeelden[TUIN]["data"][:2] == b"\xff\xd8"
    # inleren en nog eens: nu met naam
    asyncio.run(c.async_gezicht_inleren(data["gezicht_id"], "Ruud"))
    c.sessies[STATION] = _NepSessie(c, frames)
    asyncio.run(c._async_namen_uit_opname(STATION, TUIN, _pad(time.time() - 20), moment))
    data = c.hass.bus.async_fire.call_args[0][1]
    assert data["namen"] == ["Ruud"] and c.personen[TUIN][0]["namen"] == ["Ruud"] and data["gezicht_id"] is None


def test_na_drie_mislukte_opnames_niet_meer_automatisch(tmp_path):
    c = _met_sessie(tmp_path, [], code=-104)
    moment = {"tijd": int(time.time()), "zekerheid": 0.8, "aantal": 1, "bestand": None, "namen": [], "hond": False}
    for _ in range(3):
        assert c._opname_automatisch()
        asyncio.run(c._async_namen_uit_opname(STATION, TUIN, _pad(time.time()), moment))
    assert not c._opname_automatisch()


def test_frontaal_en_belichting():
    from custom_components.eufy_lokaal import gezichten as gz

    recht = gz.SJABLOON.tolist()
    assert gz.frontaal(recht, [20, 30, 92, 110]) > 0.9
    opzij = [[40, 50], [55, 50], [38, 72], [42, 92], [52, 92]]        # neus naast de ogen, ogen dicht bij elkaar
    assert gz.frontaal(opzij, [20, 30, 92, 110]) < 0.3
    assert gz.belichting(Image.new("RGB", (112, 112), (250, 250, 250))) == 0.0
    assert gz.belichting(Image.new("RGB", (112, 112), (120, 110, 100))) == 1.0


def test_een_persoon_in_beeld_geeft_hooguit_een_gezicht(monkeypatch):
    # twee personen in beeld, maar stel dat er maar één persoon was: dan één gezicht
    from custom_components.eufy_lokaal import herkenning as h

    echt = h.beoordeel

    def een_persoon(jpeg, *a):
        u = echt(jpeg, *a)
        return {**u, "personen": u["personen"][:1]}

    monkeypatch.setattr(h, "beoordeel", een_persoon)
    a = op.analyseer(_opname(BEELD, aantal=8, fps=4), {})
    assert a["personen"] == 1 and len(a["gezichten"]) == 1


def test_frontaal_en_belichting():
    from custom_components.eufy_lokaal import gezichten as gz

    recht = gz.SJABLOON.tolist()
    assert gz.frontaal(recht, [20, 30, 92, 110]) > 0.9
    opzij = [[40, 50], [55, 50], [38, 72], [42, 92], [52, 92]]        # neus naast de ogen, ogen dicht bij elkaar
    assert gz.frontaal(opzij, [20, 30, 92, 110]) < 0.3
    assert gz.belichting(Image.new("RGB", (112, 112), (250, 250, 250))) == 0.0
    assert gz.belichting(Image.new("RGB", (112, 112), (120, 110, 100))) == 1.0


def test_een_persoon_in_beeld_geeft_hooguit_een_gezicht(monkeypatch):
    from custom_components.eufy_lokaal import herkenning as h

    echt = h.beoordeel

    def een_persoon(jpeg, *a):
        u = echt(jpeg, *a)
        return {**u, "personen": u["personen"][:1]}

    monkeypatch.setattr(h, "beoordeel", een_persoon)
    a = op.analyseer(_opname(BEELD, aantal=8, fps=4), {})
    assert a["personen"] == 1 and len(a["gezichten"]) == 1
