"""v0.7.20: momentopname robuuster (verdeelde keyframes, verkeerde codec,
time-out met uitleg) en Live momentopname valt terug op het laatste beeld."""
import asyncio
from types import SimpleNamespace

import pytest

from custom_components.eufy_lokaal import video as v
from custom_components.eufy_lokaal.image import LiveMomentopname
from custom_components.eufy_lokaal.opslag import monster_opslaan

from test_stap6_live import _frames, _videobericht


def test_verdeeld_keyframe_wordt_beeld():
    kf = _frames()[0]
    stuk = len(kf) // 5 + 1
    brokken = [kf[i:i + stuk] for i in range(0, len(kf), stuk)] + _frames()[1:4]
    assert len(brokken) > 4
    assert v.frames_naar_jpeg(brokken)[:2] == b"\xff\xd8"


def test_verkeerde_codec_toch_beeld():
    # H.264-data die als H.265 is herkend: de andere codec wordt geprobeerd
    assert v.frames_naar_jpeg(_frames()[:4], codec="hevc")[:2] == b"\xff\xd8"


def test_onzin_geeft_valueerror_met_uitleg():
    with pytest.raises(ValueError, match="niet te decoderen"):
        v.frames_naar_jpeg([b"\x00\x00\x00\x01\x65" + b"\x13" * 500])


def test_time_out_zegt_wat_er_binnenkwam():
    async def _proef():
        stream = v.LiveStream("T8416", 0, lambda s: None, lambda: None)

        async def stuur():
            for _ in range(50):
                if stream.sleutel is not None:
                    break
                await asyncio.sleep(0.02)
            onzin = b"\x00\x00\x00\x01\x65" + b"\x13" * 500
            for _ in range(12):
                stream.bij_frame(_videobericht(onzin, stream.sleutel, b"Q" * 16), 1)
                await asyncio.sleep(0)

        t = asyncio.ensure_future(stuur())
        with pytest.raises(v.GeenBeeld) as e:
            await v.momentopname(stream, timeout=1)
        await t
        return e.value

    fout = asyncio.run(_proef())
    assert isinstance(fout, TimeoutError)
    assert "12 videoframes" in str(fout) and "niet te decoderen" in str(fout)
    assert fout.monster.startswith(b"\x00\x00\x00\x01")


def test_monster_opslaan(tmp_path):
    monster_opslaan(tmp_path / "m", "T8416/../x", b"\x00\x00\x00\x01abc")
    bestanden = list((tmp_path / "m").iterdir())
    assert len(bestanden) == 1 and bestanden[0].name.endswith("_mislukt.bin")
    monster_opslaan(tmp_path / "leeg", "X", b"")
    assert not (tmp_path / "leeg").exists()


def _entiteit(momentopnamen, beelden):
    c = SimpleNamespace(momentopnamen=momentopnamen, beelden=beelden, laatste_info={})
    e = LiveMomentopname.__new__(LiveMomentopname)
    e.coordinator = c
    e._sn = "T8416"
    e._tijd = None
    return e


def test_live_valt_terug_op_laatste_beeld():
    e = _entiteit({}, {"T8416": {"data": b"\xff\xd8hb", "tijd": 1.0}})
    assert e.available and e.extra_state_attributes["bron"] == "laatste beeld (HomeBase)"
    assert asyncio.run(e.async_image()) == b"\xff\xd8hb"
    # een mislukte momentopname (alleen fout) houdt de terugval
    e = _entiteit({"T8416": {"fout": "GeenBeeld: x"}}, {"T8416": {"data": b"\xff\xd8hb", "tijd": 1.0}})
    assert e.available and e.extra_state_attributes["fout"] == "GeenBeeld: x"
    # met live beeld gaat dat voor
    e = _entiteit({"T8416": {"data": b"\xff\xd8live", "tijd": 2.0}}, {"T8416": {"data": b"\xff\xd8hb", "tijd": 1.0}})
    assert e.extra_state_attributes["bron"] == "live" and asyncio.run(e.async_image()) == b"\xff\xd8live"
    # niets: onbeschikbaar
    assert not _entiteit({}, {}).available
