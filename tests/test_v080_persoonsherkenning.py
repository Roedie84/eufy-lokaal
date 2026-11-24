"""v0.8.0: personen herkennen op het gebeurtenisbeeld, lokaal met alleen numpy.

De HomeBase 3 zegt lokaal niet of er een persoon is (v0.7.15). Daarom beoordeelt
de integratie het beeld zelf, voor elke camera behalve de deurbel."""
import asyncio
import io
import time
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image

from custom_components.eufy_lokaal import herkenning as h
from custom_components.eufy_lokaal import persoonslog as pl
from custom_components.eufy_lokaal.gebeurtenissen import is_deurbel


def _jpeg(breedte=640, hoogte=360, kleur=(90, 110, 80)) -> bytes:
    uit = io.BytesIO()
    Image.new("RGB", (breedte, hoogte), kleur).save(uit, "JPEG")
    return uit.getvalue()


# rekenstappen -----------------------------------------------------------------------

def _conv_traag(x, w, b, stap, rand, groep):
    x = np.pad(x, ((0, 0), (0, 0), (rand, rand), (rand, rand)))
    uit_k, per_groep, kh, kw = w.shape
    h_uit = (x.shape[2] - kh) // stap + 1
    b_uit = (x.shape[3] - kw) // stap + 1
    y = np.zeros((1, uit_k, h_uit, b_uit), dtype=np.float64)
    per_uit = uit_k // groep
    for k in range(uit_k):
        g = k // per_uit
        for i in range(h_uit):
            for j in range(b_uit):
                stuk = x[0, g * per_groep:(g + 1) * per_groep, i * stap:i * stap + kh, j * stap:j * stap + kw]
                y[0, k, i, j] = (stuk * w[k]).sum() + b[k]
    return y


@pytest.mark.parametrize("kanalen,uit,k,stap,rand,groep", [
    (3, 5, 3, 1, 1, 1), (4, 6, 3, 2, 1, 1), (6, 4, 1, 1, 0, 1), (6, 6, 3, 1, 1, 6), (6, 6, 3, 2, 1, 6)])
def test_convolutie_gelijk_aan_uitgeschreven_som(kanalen, uit, k, stap, rand, groep):
    r = np.random.default_rng(3)
    x = r.normal(size=(1, kanalen, 9, 11)).astype(np.float32)
    w = r.normal(size=(uit, kanalen // groep, k, k)).astype(np.float32)
    b = r.normal(size=uit).astype(np.float32)
    at = {"group": groep, "strides": [stap, stap], "pads": [rand] * 4}
    assert np.allclose(h._conv(x, w, b, at), _conv_traag(x, w, b, stap, rand, groep), atol=1e-4)


def test_convolutie_met_gewone_groepen_kan_sinds_v090():
    y = h._conv(np.ones((1, 4, 5, 5), np.float32), np.ones((4, 2, 3, 3), np.float32), None,
                {"group": 2, "pads": [1] * 4})
    assert y.shape == (1, 4, 5, 5) and y[0, 0, 2, 2] == 18


def test_maxpool_houdt_de_maat_en_pakt_het_maximum():
    x = np.arange(49, dtype=np.float32).reshape(1, 1, 7, 7)
    y = h._maxpool(x, {"kernel_shape": [5, 5], "pads": [2] * 4, "strides": [1, 1]})
    assert y.shape == x.shape and y[0, 0, 0, 0] == x[0, 0, 2, 2] and y[0, 0, 6, 6] == 48


# het model zelf ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def model():
    return h.Model(h.MODELBESTAND)


def test_modelbestand_is_er_en_klein_genoeg():
    assert h.MODELBESTAND.is_file()
    assert 5_000_000 < h.MODELBESTAND.stat().st_size < 12_000_000


def test_uitkomst_gelijk_aan_referentie(model):
    """Zelfde invoer als bij onnxruntime (volle precisie); de waarden hieronder komen daaruit."""
    x = np.random.default_rng(7).uniform(0, 255, (1, 3, 416, 416)).astype(np.float32)
    o = model.reken(x)[0]
    assert o.shape == (3549, 85)
    for i, j, verwacht in [[0, 0, 1.1333], [0, 4, 0.0], [100, 5, 0.0053], [1500, 2, 2.0087], [2703, 4, 0.0],
                           [3000, 3, 2.0376], [3400, 0, -0.349], [3548, 84, 0.0055]]:
        assert o[i, j] == pytest.approx(verwacht, abs=0.03), (i, j)
    assert float(np.abs(o).mean()) == pytest.approx(0.0605, abs=0.002)


def test_egaal_beeld_heeft_geen_persoon_en_is_snel():
    u = h.beoordeel(_jpeg())
    assert u["personen"] == [] and u["hoogste"] == 0.0
    assert (u["breedte"], u["hoogte"]) == (640, 360)
    u = h.beoordeel(_jpeg(1920, 1080))  # tweede keer: model is al geladen
    assert u["ms"] < 5000


def test_kapot_beeld_geeft_een_fout_en_geen_uitslag():
    with pytest.raises(Exception):
        h.beoordeel(b"\xff\xd8 geen jpeg")


# van modeluitvoer naar personen -----------------------------------------------------

def _ruw(*vakken):
    """Modeluitvoer met op gekozen roosterpunten een vak: (index, zekerheid, klasse, hoogte_px)."""
    ruw = np.zeros((3549, 85), dtype=np.float32)
    ruw[:, 2:4] = -5  # piepkleine vakken
    for index, zekerheid, klasse, hoogte in vakken:
        stap = 8 if index < 2704 else 16 if index < 3380 else 32
        ruw[index, 2] = np.log(hoogte / 3 / stap)
        ruw[index, 3] = np.log(hoogte / stap)
        ruw[index, 4] = 1.0
        ruw[index, 5 + klasse] = zekerheid
    return ruw


def test_persoon_boven_drempel_met_vak_als_deel_van_het_beeld():
    # roosterpunt 52*26+26 = midden van het 416-beeld; beeld 832x832 dus schaal 0,5
    p = h.personen_uit(_ruw((52 * 26 + 26, 0.9, 0, 120)), 0.5, 832, 832)
    assert len(p) == 1 and p[0]["zekerheid"] == 0.9
    x1, y1, x2, y2 = p[0]["vak"]
    assert 0.4 < x1 < x2 < 0.6 and y2 - y1 == pytest.approx(120 / 416, abs=0.01)


def test_onder_drempel_andere_klasse_en_te_klein_tellen_niet():
    assert h.personen_uit(_ruw((1378, 0.44, 0, 120)), 1.0, 416, 416) == []
    assert h.personen_uit(_ruw((1378, 0.9, 16, 120)), 1.0, 416, 416) == []       # hond
    assert h.personen_uit(_ruw((1378, 0.9, 0, 12)), 1.0, 416, 416) == []         # 3 % van de hoogte
    ruw = _ruw((1378, 0.9, 0, 120))
    ruw[1378, 5 + 16] = 0.95                                                      # hond wint van persoon
    assert h.personen_uit(ruw, 1.0, 416, 416) == []


def test_overlappende_vakken_zijn_een_persoon_en_losse_twee():
    dubbel = h.personen_uit(_ruw((1378, 0.9, 0, 120), (1379, 0.8, 0, 120)), 1.0, 416, 416)
    assert [p["zekerheid"] for p in dubbel] == [0.9]
    los = h.personen_uit(_ruw((1378 - 20, 0.7, 0, 120), (1378 + 20, 0.8, 0, 120)), 1.0, 416, 416)
    assert [p["zekerheid"] for p in los] == [0.8, 0.7]


def test_kader_tekenen_geeft_weer_een_jpeg_van_dezelfde_maat():
    uit = h.teken_vakken(_jpeg(), [{"zekerheid": 0.9, "vak": [0.4, 0.2, 0.6, 0.9]}])
    beeld = Image.open(io.BytesIO(uit))
    assert uit[:2] == b"\xff\xd8" and beeld.size == (640, 360)
    r, g, b = beeld.getpixel((int(0.4 * 640) + 1, 180))
    assert r > 200 and b < 120  # het gele kader


# logboek ----------------------------------------------------------------------------

def test_bestandsnaam_heen_en_terug_en_geen_paden():
    naam = pl.bestandsnaam("T817LT0025390096", 1795100000.7, 0.876, 2)
    assert pl.ontleed(naam) == {"sn": "T817LT0025390096", "tijd": 1795100000, "zekerheid": 0.88,
                                "aantal": 2, "bestand": naam}
    for fout in ("../secrets.yaml", "a/b_1795100000_088_1.jpg", "x_1795100000_088_1.jpg.tmp", "", None,
                 "momentopname_T8416.jpg"):
        assert pl.ontleed(fout) is None, fout
    assert "/" not in pl.bestandsnaam("T8/../x", 1795100000, 0.5, 1)


def test_opslaan_lijst_lezen_en_opruimen(tmp_path):
    nu = 1795100000
    oud = pl.opslaan(tmp_path, "CAM1", nu - 15 * 86400, 0.6, 1, b"\xff\xd8oud", nu=nu - 15 * 86400)
    a = pl.opslaan(tmp_path, "CAM1", nu - 3600, 0.7, 1, b"\xff\xd8a", nu=nu - 3600)
    b = pl.opslaan(tmp_path, "CAM1", nu, 0.9, 2, b"\xff\xd8b", nu=nu)          # ruimt het oude op
    pl.opslaan(tmp_path, "CAM2", nu, 0.8, 1, b"\xff\xd8c", nu=nu)
    assert [m["bestand"] for m in pl.lijst(tmp_path, "CAM1")] == [b, a]
    assert len(pl.lijst(tmp_path)) == 3 and pl.lees(tmp_path, oud) is None
    assert pl.lees(tmp_path, b) == b"\xff\xd8b"
    assert pl.lees(tmp_path, "../" + b) is None
    assert not list(tmp_path.glob("*.tmp"))
    assert pl.lijst(tmp_path / "bestaat_niet") == []


def test_niet_meer_dan_het_maximum_per_camera(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "MAX_PER_CAMERA", 3)
    for i in range(5):
        pl.opslaan(tmp_path, "CAM1", 1795100000 + i, 0.6, 1, b"\xff\xd8", nu=1795100000 + i)
    assert [m["tijd"] - 1795100000 for m in pl.lijst(tmp_path, "CAM1")] == [4, 3, 2]


def test_beeldtijd_uit_het_pad_van_de_homebase():
    from datetime import timezone

    t = pl.beeldtijd_uit_pad("/zx/hdd_data0/Camera03/202611/20261119145430/snapshort.jpg", timezone.utc)
    assert time.gmtime(t)[:6] == (2026, 11, 19, 14, 54, 30)
    assert pl.beeldtijd_uit_pad("/zx/iets/snapshort.jpg", timezone.utc) is None
    assert pl.beeldtijd_uit_pad("/zx/20269999999999/x.jpg", timezone.utc) is None


def test_oud_beeld_na_herstart_is_geen_nieuwe_gebeurtenis():
    nu = 1795100000.0
    assert pl.is_vers(nu - 60, nu, eerste_beeld=True)
    assert not pl.is_vers(nu - 3600, nu, eerste_beeld=False)
    assert not pl.is_vers(None, nu, eerste_beeld=True)      # geen tijd en het eerste beeld: niet melden
    assert pl.is_vers(None, nu, eerste_beeld=False)


# coördinator ------------------------------------------------------------------------

DEURBEL, TUIN, STATION = "T821451025211C7C", "T817LT0025390096", "T8030T2325281762"


def test_deurbel_herkend_als_deurbel():
    assert is_deurbel({"device_sn": DEURBEL, "device_type": 94})
    assert not is_deurbel({"device_sn": TUIN, "device_type": 10031})
    assert not is_deurbel({"device_sn": "T8416P0000000000", "device_type": 104})


def _coordinator(tmp_path, uitslag):
    from custom_components.eufy_lokaal.coordinator import EufyLokaalCoordinator

    async def _uitvoeren(functie, *args):
        if functie is h.beoordeel:
            if isinstance(uitslag, Exception):
                raise uitslag
            return uitslag
        return functie(*args)

    c = object.__new__(EufyLokaalCoordinator)
    taken = []
    c.hass = NS(config=NS(path=lambda *d: str(tmp_path.joinpath(*d))),
                async_add_executor_job=_uitvoeren, bus=MagicMock(), loop=MagicMock(),
                async_create_background_task=lambda taak, naam: taken.append(taak))
    c.stations = {STATION: {"station_sn": STATION}}
    c.apparaten = {DEURBEL: {"device_sn": DEURBEL, "station_sn": STATION, "device_type": 94},
                   TUIN: {"device_sn": TUIN, "station_sn": STATION, "device_type": 10031}}
    c.tellingen, c.detecties, c.laatste_gebeurtenis, c._bij_gebeurtenis = {}, {}, {}, []
    c.herkenning, c.personen, c.persoonsbeelden, c._herkend_pad = {}, {}, {}, {}
    c.beelden, c._beeld_voor, c.sessies = {}, {}, {}
    c._herken_slot = asyncio.Lock()
    c.live_token = "geheim"
    c.bekenden, c.momentopnamen = {"personen": {}, "onbekend": {}}, {}

    async def _geen_live(sn, reden="gevraagd"):
        return False
    c.async_momentopname = _geen_live
    c.async_update_listeners = MagicMock()
    c._taken = taken
    return c


def _pad(tijd: float) -> str:
    return "/zx/hdd_data0/Camera03/202611/" + time.strftime("%Y%m%d%H%M%S", time.localtime(tijd)) + "/snapshort.jpg"


EEN_PERSOON = {"personen": [{"zekerheid": 0.82, "vak": [0.4, 0.2, 0.6, 0.9]}], "hoogste": 0.82,
               "breedte": 640, "hoogte": 360, "ms": 210}
NIEMAND = {"personen": [], "hoogste": 0.31, "breedte": 640, "hoogte": 360, "ms": 190}


def _draai(c, sn, pad, eerste=False):
    import zoneinfo
    from unittest.mock import patch

    lokaal = zoneinfo.ZoneInfo(time.tzname[0]) if time.tzname[0] in zoneinfo.available_timezones() else None
    from datetime import datetime
    tz = datetime.now().astimezone().tzinfo if lokaal is None else lokaal
    with patch("homeassistant.util.dt.get_default_time_zone", return_value=tz):
        asyncio.run(c._async_herken(STATION, sn, pad, _jpeg(), eerste))


def test_elke_camera_doet_mee_tenzij_de_schakelaar_uit_staat(tmp_path):
    # v0.11.0: ook de deurbel (Ruud 19-11); per camera uit te zetten
    c = _coordinator(tmp_path, EEN_PERSOON)
    assert c.herkent(TUIN) and c.herkent(DEURBEL) and not c.herkent(STATION) and not c.herkent("onbekend")
    c.zet_herkenning(DEURBEL, False)
    assert not c.herkent(DEURBEL) and c.kan_herkennen(DEURBEL) and c.herkent(TUIN)
    c.zet_herkenning(DEURBEL, True)
    assert c.herkent(DEURBEL)


def test_persoon_zet_detectie_aan_bewaart_beeld_en_meldt(tmp_path):
    c = _coordinator(tmp_path, EEN_PERSOON)
    _draai(c, TUIN, _pad(time.time() - 30))
    assert c.detectie_aan(TUIN, "persoon") and c.detectie_aan(TUIN, "beweging")
    moment = c.personen[TUIN][0]
    assert moment["zekerheid"] == 0.82 and moment["aantal"] == 1
    map_ = tmp_path / ".storage" / "eufy_lokaal" / "personen"
    assert (map_ / moment["bestand"]).read_bytes()[:2] == b"\xff\xd8"
    assert c.persoonsbeelden[TUIN]["data"][:2] == b"\xff\xd8"
    naam, data = c.hass.bus.async_fire.call_args[0]
    assert naam == "eufy_lokaal_gebeurtenis" and data["soort"] == "onbekend_persoon"
    assert data["bron"] == "beeldherkenning" and data["zekerheid"] == 0.82
    assert data["beeld"] == f"/api/eufy_lokaal/persoon/geheim/{moment['bestand']}"
    t = c.tellingen["herkenning"]
    assert {k: t[k] for k in ("beoordeeld", "met_persoon", "fouten", "ms")} == \
        {"beoordeeld": 1, "met_persoon": 1, "fouten": 0, "ms": [210]}


def test_geen_persoon_doet_niets_maar_onthoudt_de_uitslag(tmp_path):
    c = _coordinator(tmp_path, NIEMAND)
    _draai(c, TUIN, _pad(time.time() - 30))
    assert not c.detectie_aan(TUIN, "persoon") and TUIN not in c.personen
    assert c.herkenning[TUIN]["hoogste"] == 0.31 and c.herkenning[TUIN]["aantal"] == 0
    c.hass.bus.async_fire.assert_not_called()
    c.async_update_listeners.assert_called()


def test_oud_beeld_met_persoon_geeft_geen_melding(tmp_path):
    c = _coordinator(tmp_path, EEN_PERSOON)
    _draai(c, TUIN, _pad(time.time() - 2 * 3600), eerste=True)
    assert not c.detectie_aan(TUIN, "persoon") and TUIN not in c.personen
    assert c.herkenning[TUIN]["vers"] is False and c.herkenning[TUIN]["aantal"] == 1
    c.hass.bus.async_fire.assert_not_called()


def test_fout_bij_beoordelen_raakt_verder_niets(tmp_path):
    c = _coordinator(tmp_path, ValueError("kapot beeld"))
    _draai(c, TUIN, _pad(time.time()))
    assert "kapot beeld" in c.herkenning[TUIN]["fout"] and c.tellingen["herkenning"]["fouten"] == 1
    assert not c.detectie_aan(TUIN, "persoon")


def test_niet_te_bewaren_beeld_telt_nog_steeds_als_persoon(tmp_path):
    c = _coordinator(tmp_path, EEN_PERSOON)
    (tmp_path / ".storage").write_text("geen map")  # opslaan mislukt
    _draai(c, TUIN, _pad(time.time()))
    assert c.detectie_aan(TUIN, "persoon") and c.personen[TUIN][0]["bestand"] is None
    assert c.hass.bus.async_fire.call_args[0][1]["beeld"] is None


def test_nieuw_beeld_start_een_beoordeling_alleen_waar_het_moet(tmp_path, monkeypatch):
    from custom_components.eufy_lokaal import berichten

    c = _coordinator(tmp_path, NIEMAND)
    monkeypatch.setattr(berichten, "ontsleutel_afbeelding", lambda did, ruw: _jpeg())
    for sn, pad in ((TUIN, "/a/20261119145430/s.jpg"), (DEURBEL, "/b/20261119145431/s.jpg")):
        c._beeld_voor[pad] = (sn, time.time())
        c._bij_afbeelding(STATION, {"bestand": pad, "ruw": b"x"})
    assert len(c._taken) == 2 and TUIN in c.beelden and DEURBEL in c.beelden
    # hetzelfde beeld nog eens: niet opnieuw
    c._beeld_voor["/a/20261119145430/s.jpg"] = (TUIN, time.time())
    c._bij_afbeelding(STATION, {"bestand": "/a/20261119145430/s.jpg", "ruw": b"x"})
    assert len(c._taken) == 2
    c.zet_herkenning(TUIN, False)   # uit: geen beoordeling
    c._beeld_voor["/a/20261119145500/s.jpg"] = (TUIN, time.time())
    c._bij_afbeelding(STATION, {"bestand": "/a/20261119145500/s.jpg", "ruw": b"x"})
    assert len(c._taken) == 2
    for taak in c._taken:
        taak.close()


def test_logboek_teruglezen_na_herstart_en_overzicht(tmp_path):
    c = _coordinator(tmp_path, NIEMAND)
    map_ = tmp_path / ".storage" / "eufy_lokaal" / "personen"
    nu = int(time.time())
    pl.opslaan(map_, TUIN, nu - 86400 * 2, 0.6, 1, b"\xff\xd8eergisteren", nu=nu)
    nieuw = pl.opslaan(map_, TUIN, nu - 5, 0.9, 2, b"\xff\xd8net", nu=nu)
    pl.opslaan(map_, DEURBEL, nu, 0.9, 1, b"\xff\xd8", nu=nu)
    asyncio.run(c.async_personen_laden())
    assert [m["bestand"] for m in c.personen[TUIN]][0] == nieuw and len(c.personen[DEURBEL]) == 1
    assert c.persoonsbeelden[TUIN]["data"] == b"\xff\xd8net"
    o = c.personen_overzicht(TUIN)
    assert o["bewaard"] == 2 and o["vandaag"] in (0, 1)  # 5 s geleden kan net gisteren zijn
    assert o["laatste"][0]["beeld"].endswith(f"/{nieuw}") and o["laatste_uitslag"] is None


def test_sensor_en_beeld_alleen_live_niet_in_de_recorder():
    from custom_components.eufy_lokaal.sensor import PersonenSensor

    assert {"laatste", "laatste_uitslag"} <= PersonenSensor._unrecorded_attributes
