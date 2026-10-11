"""Stap 6: livestream."""
import asyncio
import json
import re
import struct
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from custom_components.eufy_lokaal import p2p_sessie as ps
from custom_components.eufy_lokaal import video as v
from custom_components.eufy_lokaal.p2p import bericht
from custom_components.eufy_lokaal.video_opdrachten import startopdracht

H264 = (Path(__file__).parent / "testbeeld.h264").read_bytes()


def _frames():
    pos = [m.start() for m in re.finditer(b"\x00\x00\x00\x01", H264)]
    nals = [H264[p:q] for p, q in zip(pos, pos[1:] + [len(H264)])]
    uit, cur = [], b""
    for n in nals:
        t = n[4] & 0x1F
        if t in (1, 5) and any((x[4] & 0x1F) in (1, 5) for x in re.split(b"(?=\x00\x00\x00\x01)", cur) if len(x) > 4):
            uit.append(cur)
            cur = b""
        if t == 7 and cur:
            uit.append(cur)
            cur = b""
        cur += n
    return uit + [cur]


def _videobericht(frame: bytes, sleutel: v.RsaSleutel | None, aes: bytes | None) -> bytes:
    kop = struct.pack("<I", len(frame)) + bytes([1 if v._is_idr(frame) else 0, 1]) + struct.pack(
        "<HHHH", 0, 15, 640, 360) + (1234).to_bytes(6, "little") + b"\x00\x00"
    if sleutel is None:
        return kop + frame
    e = Cipher(algorithms.AES(aes), modes.ECB()).encryptor()
    blok = frame[:128]
    blok = blok[: len(blok) // 16 * 16]
    versleuteld = e.update(blok) + e.finalize() + frame[len(blok):]
    return kop + sleutel.versleutel(aes) + b"\x00" + versleuteld


def test_frame_ontsleutelen():
    s = v.RsaSleutel()
    assert len(bytes.fromhex(s.publiek_hex())) == 128
    f0 = _frames()[0]
    f = v.lees_videoframe(_videobericht(f0, s, b"K" * 16), 1, s)
    assert f.data == f0 and f.keyframe and (f.breedte, f.hoogte, f.fps) == (640, 360, 15)
    assert v.lees_videoframe(_videobericht(f0, None, None), 0, None).data == f0


def test_mpegts_speelbaar(tmp_path):
    uit = bytearray()
    m = None
    for fr in _frames():
        f = v.Frame(fr, v._is_idr(fr), 640, 360, 15, 0, 1)
        if m is None:
            m = v.TsMuxer(uit.extend, 640, 360, 15)
        m.voeg_toe(f)
    m.sluit()
    pad = tmp_path / "x.ts"
    pad.write_bytes(bytes(uit))
    r = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries",
                        "stream=codec_name,width,nb_read_frames", "-of", "json", str(pad)],
                       capture_output=True, text=True, check=True)
    s = json.loads(r.stdout)["streams"][0]
    assert s["codec_name"] == "h264" and s["width"] == 640 and int(s["nb_read_frames"]) >= 40


def test_startopdrachten_per_type():
    o, t, r = startopdracht(94, 1, "adm", "ab" * 128, "T8030")
    assert o == ps.CMD_DOORBELL_SET_PAYLOAD and r == "deurbel_payload_1"
    assert json.loads(t)["data"]["encryptkey"] == "ab" * 128
    o, t, r = startopdracht(104, 2, "adm", "cd", "T8030")
    assert o == ps.CMD_SET_PAYLOAD and json.loads(t)["mChannel"] == 2 and r == "set_payload_s350"
    o, t, r = startopdracht(999, 3, "adm", "ef", "T8030")
    assert r == "set_payload" and json.loads(t)["cmd"] == 1003
    # alle startopdrachten komen door de controle; iets anders via 1700 niet
    for typ in (94, 104, 32, 999):
        o, t, _ = startopdracht(typ, 1, "a", "00", "T")
        ps.opdracht_met_tekst(0, o, t, b"x" * 16, 1, 1)
    with pytest.raises(ValueError):
        ps.opdracht_met_tekst(0, ps.CMD_DOORBELL_SET_PAYLOAD, json.dumps({"commandType": 1001, "data": {}}))


def test_volgorde_herstellen():
    s = ps.P2PSessie("T8030T2325281762", "EUPRDMB-241997-DGTKC", "1.2.3.4", 1)
    verwerkt = []
    s._verwerk_data = lambda soort, nr, data: (verwerkt.append(nr), s._verwacht.__setitem__(soort, (nr + 1) % 65536))
    s._verwacht[1] = 10

    def pak(nr):
        return b"\xf1\xd0\x00\x08\xd1\x01" + struct.pack(">H", nr)

    s._data(pak(12), None)
    s._data(pak(11), None)
    assert verwerkt == []
    s._data(pak(10), None)
    assert verwerkt == [10, 11, 12]
    s._data(pak(11), None)  # dubbel
    assert verwerkt == [10, 11, 12]


def test_stream_naar_kijker():
    async def _proef():
        gestart, gestopt = [], []
        stream = v.LiveStream("T8113", 2, gestart.append, lambda: gestopt.append(1))
        ontvangen = bytearray()

        async def schrijf(d):
            ontvangen.extend(d)

        taak = asyncio.ensure_future(v.stuur_ts(stream, schrijf))
        for _ in range(50):
            if stream.sleutel is not None and gestart:
                break
            await asyncio.sleep(0.02)
        assert gestart and len(bytes.fromhex(gestart[0])) == 128
        for fr in _frames()[:20]:
            stream.bij_frame(_videobericht(fr, stream.sleutel, b"Q" * 16), 1)
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        stream.stop()
        await taak
        return stream, ontvangen, gestopt

    stream, ontvangen, gestopt = asyncio.run(_proef())
    assert stream.frames == 20 and stream.fouten == 0 and gestopt == [1]
    assert ontvangen[:1] == b"\x47" and len(ontvangen) > 5000  # MPEG-TS sync-byte


def test_rust_na_maximale_duur():
    async def _proef():
        stream = v.LiveStream("T8113", 2, lambda s: None, lambda: None)
        k = await stream.kijker_erbij()
        stream.gestart -= v.MAX_DUUR_S + 1
        stream.bij_frame(_videobericht(_frames()[0], stream.sleutel, b"Q" * 16), 1)
        assert not stream.actief and stream.in_rust
        stream.kijker_weg(k)
        with pytest.raises(ConnectionError):
            await stream.kijker_erbij()
    asyncio.run(_proef())


AAC = (Path(__file__).parent / "testgeluid.aac").read_bytes()


def test_geluid_in_mpegts(tmp_path):
    frames = v.adts_frames(AAC)
    assert len(frames) == 48 and v.adts_formaat(frames[0]).frequentie == 16000
    # audiobericht: 16 bytes kop + een paar ADTS-frames achter elkaar
    bericht_ = struct.pack("<I", 0) + b"\x00\x00" + b"\x00" * 10 + b"".join(frames[:3])
    assert v.lees_audioframe(bericht_) == frames[:3]

    async def _proef():
        stream = v.LiveStream("T8214", 0, lambda s: None, lambda: None)
        ontvangen = bytearray()

        async def schrijf(d):
            ontvangen.extend(d)

        taak = asyncio.ensure_future(v.stuur_ts(stream, schrijf))
        for _ in range(50):
            if stream.sleutel is not None:
                break
            await asyncio.sleep(0.02)
        vframes = _frames()
        stream.bij_audio(struct.pack("<I", 0) + b"\x00" * 12 + frames[0])
        for i, fr in enumerate(vframes[:30]):
            stream.bij_frame(_videobericht(fr, stream.sleutel, b"Q" * 16), 1)
            stream.bij_audio(struct.pack("<I", 0) + b"\x00" * 12 + frames[1 + i])
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        stream.stop()
        await taak
        return ontvangen

    data = asyncio.run(_proef())
    pad = tmp_path / "av.ts"
    pad.write_bytes(bytes(data))
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,sample_rate",
                        "-of", "json", str(pad)], capture_output=True, text=True, check=True)
    namen = sorted(s["codec_name"] for s in json.loads(r.stdout)["streams"])
    assert namen == ["aac", "h264"]


def test_momentopname_jpeg():
    async def _proef():
        stops = []
        stream = v.LiveStream("T8214", 0, lambda s: None, lambda: stops.append(1))

        async def stuur():
            for _ in range(50):
                if stream.sleutel is not None:
                    break
                await asyncio.sleep(0.02)
            for fr in _frames()[1:20]:  # eerst geen keyframe
                stream.bij_frame(_videobericht(fr, stream.sleutel, b"Q" * 16), 1)
            stream.bij_frame(_videobericht(_frames()[0], stream.sleutel, b"Q" * 16), 1)

        t = asyncio.ensure_future(stuur())
        jpeg = await v.momentopname(stream, timeout=5)
        await t
        return jpeg, stream, stops

    jpeg, stream, stops = asyncio.run(_proef())
    assert jpeg[:2] == b"\xff\xd8" and len(jpeg) > 2000
    assert not stream.actief and stops == [1]  # meteen gestopt (batterij)


def test_hevc_herkennen_en_jpeg():
    data = (Path(__file__).parent / "testbeeld.hevc").read_bytes()
    assert v.codec_van(data) == "hevc" and v.codec_van(_frames()[0]) == "h264"
    assert v._is_idr(data, "hevc")
    # alles in één keer: VPS/SPS/PPS + frames
    jpeg = v.frames_naar_jpeg([data], codec="hevc")
    assert jpeg[:2] == b"\xff\xd8"
    # los keyframe zonder SPS/PPS + parametersets ervoor
    sets = v.parametersets(_frames()[0])
    assert sets and v.frames_naar_jpeg([_frames()[0]])[:2] == b"\xff\xd8"
