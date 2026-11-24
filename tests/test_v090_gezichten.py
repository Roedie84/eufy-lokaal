"""v0.9.0: wie staat er in beeld? Gezichten zoeken, vergelijken en inleren.

Het testbeeld (tests/testbeeld_personen.jpg, 1280x720, twee personen) komt uit de
voorbeelden van Ultralytics YOLOv5 (AGPL-3.0, alleen als testbeeld gebruikt)."""
import asyncio
import io
import json
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image

from custom_components.eufy_lokaal import bekenden as bk
from custom_components.eufy_lokaal import gezichten as gz
from custom_components.eufy_lokaal import herkenning as h
from custom_components.eufy_lokaal import persoonslog as pl
from custom_components.eufy_lokaal import rekenen
from custom_components.eufy_lokaal import lees_knop

BEELD = (Path(__file__).parent / "testbeeld_personen.jpg").read_bytes()


@pytest.fixture(scope="module")
def twee():
    """De twee gezichten uit het testbeeld: vak, punten en kenmerken."""
    im = Image.open(io.BytesIO(BEELD))
    gs = sorted(gz.zoek_gezichten(im), key=lambda g: g["vak"][0])
    return [{**g, "v": gz.kenmerken(gz.recht_zetten(im, g["punten"]))} for g in gs]


# rekenwerk ----------------------------------------------------------------------------

def test_nieuwe_rekenstappen():
    x = np.array([[[[-2.0, 3.0]]]], dtype=np.float32)
    assert np.array_equal(rekenen._maxpool(np.arange(16, dtype=np.float32).reshape(1, 1, 4, 4),
                                           {"kernel_shape": [2, 2], "strides": [2, 2]}), [[[[5, 7], [13, 15]]]])
    # gewone groepen (2 groepen van 2 kanalen) gelijk aan twee losse convoluties
    r = np.random.default_rng(1)
    a = r.normal(size=(1, 4, 5, 5)).astype(np.float32)
    w = r.normal(size=(6, 2, 3, 3)).astype(np.float32)
    y = rekenen._conv(a, w, None, {"group": 2, "pads": [1] * 4})
    los = np.concatenate([rekenen._conv(a[:, :2], w[:3], None, {"pads": [1] * 4}),
                          rekenen._conv(a[:, 2:], w[3:], None, {"pads": [1] * 4})], axis=1)
    assert np.allclose(y, los, atol=1e-5)
    assert x.shape == (1, 1, 1, 2)


def test_gezichtsmodellen_zijn_er_en_klein_genoeg():
    assert 100_000 < (gz.MAP / "gezicht_zoek.npz").stat().st_size < 1_000_000
    assert 10_000_000 < (gz.MAP / "gezicht_ken.npz").stat().st_size < 25_000_000


# zoeken en herkennen (uitkomsten getoetst tegen OpenCV FaceDetectorYN/FaceRecognizerSF) --

def test_twee_gezichten_gevonden_op_de_plek_van_opencv(twee):
    # OpenCV 5.1 op hetzelfde beeld: (919, 101, b 131) en (545, 266, b 117)
    assert len(twee) == 2
    links, rechts = twee
    assert links["vak"][0] == pytest.approx(545, abs=3) and links["vak"][1] == pytest.approx(266, abs=3)
    assert rechts["vak"][0] == pytest.approx(919, abs=3) and rechts["vak"][1] == pytest.approx(101, abs=3)
    assert all(g["zekerheid"] > 0.8 for g in twee) and all(len(g["punten"]) == 5 for g in twee)


def test_kenmerken_genormaliseerd_en_verschillende_mensen_lijken_niet(twee):
    a, b = twee[0]["v"], twee[1]["v"]
    assert np.linalg.norm(a) == pytest.approx(1, abs=1e-6) and a.shape == (128,)
    assert float(a @ b) < 0.2


def test_zelfde_gezicht_kleiner_wordt_herkend(twee):
    klein = Image.open(io.BytesIO(BEELD)).resize((1280 // 3, 720 // 3))
    for g in gz.zoek_gezichten(klein):
        v = gz.kenmerken(gz.recht_zetten(klein, g["punten"]))
        naam, gelijk = gz.vergelijk(v, {"links": [twee[0]["v"].tolist()], "rechts": [twee[1]["v"].tolist()]})
        verwacht = "links" if g["vak"][0] < 640 // 3 else "rechts"
        assert naam == verwacht and gelijk > 0.8


def test_vergelijken_onder_drempel_geeft_geen_naam():
    v = np.zeros(128)
    v[0] = 1
    w = np.zeros(128)
    w[1] = 1
    assert gz.vergelijk(v, {"A": [w.tolist()]}) == (None, 0.0)
    assert gz.vergelijk(v, {}) == (None, -1.0)
    assert gz.vergelijk(v, {"A": [w.tolist(), v.tolist()], "B": []}) == ("A", 1.0)


def test_rechtzetten_legt_de_ogen_op_de_vaste_plek():
    van = gz.SJABLOON * 2.5 + [300, 120]          # gezicht 2,5x zo groot, verschoven
    m = gz._gelijkvormig(van, gz.SJABLOON)
    terug = (m[:, :2] @ van.T).T + m[:, 2]
    assert np.allclose(terug, gz.SJABLOON, atol=1e-6)


def test_hele_keten_vanaf_de_persoonsvakken(twee):
    personen = h.beoordeel(BEELD)["personen"]
    uit = gz.gezichten_in(BEELD, personen, {"Links": [twee[0]["v"].tolist()]})
    namen = sorted((x["naam"] or "-") for x in uit)
    assert namen == ["-", "Links"]
    for x in uit:
        assert x["uitsnede"][:2] == b"\xff\xd8" and Image.open(io.BytesIO(x["uitsnede"])).size == (112, 112)
        assert len(x["kenmerken"]) == 128 and x["gezicht_px"] > 50


def test_persoon_zonder_gezicht_wordt_overgeslagen():
    leeg = io.BytesIO()
    Image.new("RGB", (640, 360), (90, 110, 80)).save(leeg, "JPEG")
    assert gz.gezichten_in(leeg.getvalue(), [{"vak": [0.4, 0.2, 0.6, 0.9]}], {}) == []
    assert gz.gezichten_in(leeg.getvalue(), [{"vak": [0.5, 0.5, 0.51, 0.52]}], {}) == []   # te klein


# bekenden --------------------------------------------------------------------------

def test_nette_naam():
    assert bk.nette_naam("  jill ") == "Jill"
    assert bk.nette_naam("Onbekend") is None and bk.nette_naam("") is None and bk.nette_naam("!!") is None
    assert len(bk.nette_naam("x" * 80)) == 30


def test_onbekend_inleren_vergeten_en_bewaren(tmp_path):
    data = bk.leeg()
    v = [0.1] * 128
    i = bk.onbekend_erbij(tmp_path, data, v, b"\xff\xd8gezicht", "CAM", 1000.0)
    assert (tmp_path / f"gezicht_{i}.jpg").exists() and bk.GEZICHT_NAAM.match(f"gezicht_{i}.jpg")
    assert pl.lees(tmp_path, f"gezicht_{i}.jpg") == b"\xff\xd8gezicht"
    assert bk.inleren(tmp_path, data, i, "jill") == "Jill"
    assert not (tmp_path / f"gezicht_{i}.jpg").exists() and data["onbekend"] == {}
    j = bk.onbekend_erbij(tmp_path, data, v, b"\xff\xd8", "CAM", 1001.0)
    assert bk.inleren(tmp_path, data, j, "JILL") == "Jill"               # zelfde persoon
    k = bk.onbekend_erbij(tmp_path, data, v, b"\xff\xd8", "CAM", 1002.0)
    assert bk.inleren(tmp_path, data, k, "onbekend") is None              # weggegooid
    assert bk.inleren(tmp_path, data, "bestaatniet", "Ruud") is None
    assert bk.aantallen(data) == {"Jill": 2}
    bk.opslaan(tmp_path, data)
    assert bk.laden(tmp_path) == data
    assert bk.vergeten(data, "jill") and data["personen"] == {} and not bk.vergeten(data, "jill")


def test_grenzen_aan_bewaren(tmp_path, monkeypatch):
    monkeypatch.setattr(bk, "MAX_ONBEKEND", 3)
    monkeypatch.setattr(bk, "MAX_PER_PERSOON", 2)
    data = bk.leeg()
    ids = [bk.onbekend_erbij(tmp_path, data, [0.0] * 128, b"\xff\xd8", "C", 1000.0 + n) for n in range(4)]
    assert set(data["onbekend"]) == set(ids[1:]) and not (tmp_path / f"gezicht_{ids[0]}.jpg").exists()
    # na 7 dagen weg
    bk.onbekend_erbij(tmp_path, data, [0.0] * 128, b"\xff\xd8", "C", 1003.0 + bk.BEWAAR_ONBEKEND_S + 10)
    assert len(data["onbekend"]) == 1
    for n in range(3):
        i = bk.onbekend_erbij(tmp_path, data, [float(n)] * 128, b"\xff\xd8", "C", 9e6)
        bk.inleren(tmp_path, data, i, "Ruud")
    assert data["personen"]["Ruud"] == [[1.0] * 128, [2.0] * 128]   # de oudste valt eraf


def test_kapot_of_vreemd_bestand_geeft_lege_bekenden(tmp_path):
    assert bk.laden(tmp_path) == bk.leeg()
    (tmp_path / bk.BESTAND).write_text("{kapot")
    assert bk.laden(tmp_path) == bk.leeg()
    (tmp_path / bk.BESTAND).write_text(json.dumps({"personen": {"Ruud": [[1] * 3, [0.5] * 128], "": [[1] * 128]},
                                                   "onbekend": {"../x": {"kenmerken": []}}}))
    assert bk.laden(tmp_path) == {"personen": {"Ruud": [[0.5] * 128]}, "onbekend": {}}


def test_logboek_bewaart_namen(tmp_path):
    n = pl.opslaan(tmp_path, "CAM", 1795100000, 0.9, 2, b"\xff\xd8", nu=1795100000, namen=["Jill", "Ruud"])
    pl.opslaan(tmp_path, "CAM", 1795100001, 0.8, 1, b"\xff\xd8", nu=1795100001)
    momenten = pl.lijst(tmp_path, "CAM")
    assert [m["namen"] for m in momenten] == [[], ["Jill", "Ruud"]]
    assert pl.lees(tmp_path, n[:-4] + ".json") is None   # het bijbestand is niet op te vragen


def test_knop_uit_de_melding():
    assert lees_knop("EUFY_GEZICHT|0a1b2c3d4e5f|Jill") == ("0a1b2c3d4e5f", "Jill")
    assert lees_knop("EUFY_GEZICHT|0a1b2c3d4e5f|Van der Berg|x") == ("0a1b2c3d4e5f", "Van der Berg|x")
    for fout in (None, "", "EMS_JA", "EUFY_GEZICHT|", "EUFY_GEZICHT||Jill", "EUFY_GEZICHT|abc"):
        assert lees_knop(fout) is None, fout


# coördinator ------------------------------------------------------------------------

TUIN, STATION = "T817LT0025390096", "T8030T2325281762"


def _coordinator(tmp_path, live: bytes | None):
    from custom_components.eufy_lokaal.coordinator import EufyLokaalCoordinator

    async def _uitvoeren(functie, *args):
        return functie(*args)

    c = object.__new__(EufyLokaalCoordinator)
    c.hass = NS(config=NS(path=lambda *d: str(tmp_path.joinpath(*d))), async_add_executor_job=_uitvoeren,
                bus=MagicMock(), loop=MagicMock(), async_create_background_task=lambda t, n: t.close())
    c.stations = {STATION: {"station_sn": STATION}}
    c.apparaten = {TUIN: {"device_sn": TUIN, "station_sn": STATION, "device_type": 10031}}
    c.tellingen, c.detecties, c.laatste_gebeurtenis, c._bij_gebeurtenis = {}, {}, {}, []
    c.herkenning, c.personen, c.persoonsbeelden, c._herkend_pad = {}, {}, {}, {}
    c.beelden, c._beeld_voor, c.sessies, c.momentopnamen = {}, {}, {}, {}
    c.bekenden = bk.leeg()
    c._herken_slot = asyncio.Lock()
    c.live_token = "geheim"
    c.async_update_listeners = MagicMock()
    c.gevraagd = []

    async def _momentopname(sn, reden="gevraagd"):
        c.gevraagd.append((sn, reden))
        if live is None:
            return False
        c.momentopnamen[sn] = {"data": live, "tijd": time.time()}
        return True
    c.async_momentopname = _momentopname
    return c


def _pad(tijd):
    return "/zx/hdd_data0/Camera03/202611/" + time.strftime("%Y%m%d%H%M%S", time.localtime(tijd)) + "/snapshort.jpg"


def _draai(c, data=BEELD):
    from datetime import datetime
    from unittest.mock import patch

    with patch("homeassistant.util.dt.get_default_time_zone", return_value=datetime.now().astimezone().tzinfo):
        asyncio.run(c._async_herken(STATION, TUIN, _pad(time.time() - 20), data, False))


def _klein(jpeg: bytes, deel: int = 3) -> bytes:
    im = Image.open(io.BytesIO(jpeg))
    uit = io.BytesIO()
    im.resize((im.width // deel, im.height // deel)).save(uit, "JPEG", quality=85)
    return uit.getvalue()


def test_onbekende_gezichten_bewaard_en_in_de_gebeurtenis(tmp_path):
    c = _coordinator(tmp_path, live=BEELD)
    _draai(c, _klein(BEELD))   # gebeurtenisbeeld klein, live beeld groot
    assert c.gevraagd == [(TUIN, "persoon")]
    naam, data = c.hass.bus.async_fire.call_args[0]
    assert data["soort"] == "onbekend_persoon" and data["namen"] == [] and data["onbekende_gezichten"] == 2
    assert data["beeldbron"] == "live" and data["gezicht_id"] in c.bekenden["onbekend"]
    assert data["gezicht_beeld"] == f"/api/eufy_lokaal/persoon/geheim/gezicht_{data['gezicht_id']}.jpg"
    assert (tmp_path / ".storage" / "eufy_lokaal" / "personen" / bk.BESTAND).exists()
    assert c.herkenning[TUIN]["onbekend"] == 2 and c.detectie_aan(TUIN, "persoon")


def test_na_inleren_wordt_de_naam_herkend(tmp_path):
    c = _coordinator(tmp_path, live=BEELD)
    _draai(c)
    ids = list(c.bekenden["onbekend"])
    assert asyncio.run(c.async_gezicht_inleren(ids[0], "jill")) == "Jill"
    assert asyncio.run(c.async_gezicht_inleren(ids[1], "Ruud")) == "Ruud"
    c.hass.bus.async_fire.reset_mock()
    _draai(c)
    data = c.hass.bus.async_fire.call_args[0][1]
    assert data["soort"] == "bekend_persoon" and sorted(data["namen"]) == ["Jill", "Ruud"]
    assert data["persoon"] in ("Jill en Ruud", "Ruud en Jill") and data["gezicht_id"] is None
    assert sorted(c.personen[TUIN][0]["namen"]) == ["Jill", "Ruud"]
    o = c.personen_overzicht(TUIN)
    assert o["ingeleerd"] == {"Jill": 1, "Ruud": 1} and sorted(o["laatste"][0]["namen"]) == ["Jill", "Ruud"]
    assert asyncio.run(c.async_persoon_vergeten("jill")) and c.bekenden["personen"].keys() == {"Ruud"}


def test_zonder_live_beeld_wel_persoon_maar_geen_gezichten(tmp_path):
    # v0.11.0: uit het gebeurtenisbeeld kwamen vlekken als 'gezicht' (19-11)
    c = _coordinator(tmp_path, live=None)
    _draai(c)
    data = c.hass.bus.async_fire.call_args[0][1]
    assert data["beeldbron"] == "gebeurtenisbeeld" and data["onbekende_gezichten"] == 0
    assert data["soort"] == "onbekend_persoon" and c.bekenden["onbekend"] == {}


def test_live_beeld_zonder_persoon_geeft_geen_gezichten(tmp_path):
    import io as _io

    leeg = _io.BytesIO()
    Image.new("RGB", (1920, 1080), (90, 110, 80)).save(leeg, "JPEG")
    c = _coordinator(tmp_path, live=leeg.getvalue())
    _draai(c)
    data = c.hass.bus.async_fire.call_args[0][1]
    assert data["beeldbron"] == "gebeurtenisbeeld" and data["onbekende_gezichten"] == 0
    assert c.tellingen["herkenning"]["live_niemand"] == 1


def test_oud_gebeurtenisbeeld_vraagt_geen_live_beeld(tmp_path):
    from datetime import datetime
    from unittest.mock import patch

    c = _coordinator(tmp_path, live=BEELD)
    with patch("homeassistant.util.dt.get_default_time_zone", return_value=datetime.now().astimezone().tzinfo):
        asyncio.run(c._async_herken(STATION, TUIN, _pad(time.time() - 240), BEELD, False))
    assert c.gevraagd == []                       # 4 minuten oud: persoon is al weg
    assert c.hass.bus.async_fire.call_args[0][1]["onbekende_gezichten"] == 0


def test_kleine_gezichten_tellen_niet_mee():
    klein = _klein(BEELD, 3)                      # gezichten ~40 px en kleiner
    personen = h.beoordeel(klein)["personen"]
    assert all(x["gezicht_px"] >= gz.MIN_GEZICHT_PX for x in gz.gezichten_in(klein, personen, {}))


def test_aanbellen_live_beeld_wordt_beoordeeld_en_niet_dubbel_gemeld(tmp_path):
    c = _coordinator(tmp_path, live=BEELD)
    asyncio.run(c._async_herken_live(TUIN, BEELD))
    data = c.hass.bus.async_fire.call_args[0][1]
    assert data["beeldbron"] == "live" and data["onbekende_gezichten"] == 2 and c.gevraagd == []
    aantal = c.hass.bus.async_fire.call_count
    _draai(c)                                     # het gebeurtenisbeeld van dezelfde opname komt later
    assert c.hass.bus.async_fire.call_count == aantal and c.herkenning[TUIN]["al_gemeld"]


def test_fout_bij_gezichten_raakt_de_detectie_niet(tmp_path, monkeypatch):
    c = _coordinator(tmp_path, live=BEELD)
    monkeypatch.setattr(gz, "gezichten_in", MagicMock(side_effect=RuntimeError("stuk")))
    _draai(c)
    assert c.detectie_aan(TUIN, "persoon") and c.tellingen["herkenning"]["gezicht_fouten"] == 1
    assert c.hass.bus.async_fire.call_args[0][1]["namen"] == []


def test_bekenden_teruglezen_na_herstart(tmp_path):
    c = _coordinator(tmp_path, live=BEELD)
    _draai(c)
    i = next(iter(c.bekenden["onbekend"]))
    asyncio.run(c.async_gezicht_inleren(i, "Majorie"))
    nieuw = _coordinator(tmp_path, live=BEELD)
    asyncio.run(nieuw.async_personen_laden())
    assert list(nieuw.bekenden["personen"]) == ["Majorie"] and len(nieuw.bekenden["onbekend"]) == 1
    assert nieuw.personen[TUIN][0]["namen"] == []


def test_lijst_wachtende_gezichten_voor_het_dashboard(tmp_path):
    from custom_components.eufy_lokaal.sensor import GezichtenSensor

    c = _coordinator(tmp_path, live=BEELD)
    c.apparaten[TUIN]["device_name"] = "Achtertuin"
    _draai(c)
    lijst = c.gezichten_wachtend()
    assert len(lijst) == 2 and {x["camera"] for x in lijst} == {"Achtertuin"}
    assert all("/gezicht_" in x["beeld"] for x in lijst)
    assert c.gezichten_wachtend(1) == lijst[:1]
    assert {"wachtend", "ingeleerd"} <= GezichtenSensor._unrecorded_attributes
