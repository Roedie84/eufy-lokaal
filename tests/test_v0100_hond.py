"""v0.10.0: honden herkennen met hetzelfde model als personen."""
import asyncio
import time

import pytest

from custom_components.eufy_lokaal import herkenning as h
from custom_components.eufy_lokaal import persoonslog as pl

from test_v080_persoonsherkenning import STATION, TUIN, _coordinator, _draai, _pad, _ruw  # noqa: F401


def test_hond_uit_de_modeluitvoer_en_niet_als_persoon():
    ruw = _ruw((1378, 0.9, 16, 60))
    honden = h.personen_uit(ruw, 1.0, 416, 416, h.HOND_DREMPEL, h.HOND_MIN_HOOGTE, h.KLASSE_HOND)
    assert len(honden) == 1 and honden[0]["zekerheid"] == 0.9
    assert h.personen_uit(ruw, 1.0, 416, 416) == []
    # een kleine hond (4 % van de hoogte) telt, een nog kleinere niet
    assert h.personen_uit(_ruw((1378, 0.9, 16, 17)), 1.0, 416, 416, 0.45, h.HOND_MIN_HOOGTE, 16)
    assert not h.personen_uit(_ruw((1378, 0.9, 16, 10)), 1.0, 416, 416, 0.45, h.HOND_MIN_HOOGTE, 16)


def test_beoordeel_geeft_ook_honden():
    from test_v080_persoonsherkenning import _jpeg

    u = h.beoordeel(_jpeg())
    assert u["honden"] == [] and u["personen"] == []


def test_kader_om_de_hond_is_blauw():
    import io

    from PIL import Image

    from test_v080_persoonsherkenning import _jpeg

    uit = h.teken_vakken(_jpeg(), [], [{"zekerheid": 0.8, "vak": [0.2, 0.5, 0.4, 0.9]}])
    r, g, b = Image.open(io.BytesIO(uit)).getpixel((int(0.2 * 640) + 1, int(0.7 * 360)))
    assert b > 200 and r < 80


def test_logboek_onthoudt_de_hond(tmp_path):
    pl.opslaan(tmp_path, "CAM", 1795100000, 0.7, 0, b"\xff\xd8", nu=1795100000, hond=True)
    pl.opslaan(tmp_path, "CAM", 1795100001, 0.8, 1, b"\xff\xd8", nu=1795100001, namen=["Ruud"], hond=True)
    pl.opslaan(tmp_path, "CAM", 1795100002, 0.8, 1, b"\xff\xd8", nu=1795100002)
    m = pl.lijst(tmp_path, "CAM")
    assert [(x["aantal"], x["namen"], x["hond"]) for x in m] == [(1, [], False), (1, ["Ruud"], True), (0, [], True)]


HOND = {"personen": [], "honden": [{"zekerheid": 0.77, "vak": [0.2, 0.5, 0.4, 0.9]}], "hoogste": 0.1,
        "breedte": 640, "hoogte": 360, "ms": 200}
BEIDE = {"personen": [{"zekerheid": 0.82, "vak": [0.4, 0.2, 0.6, 0.9]}],
         "honden": [{"zekerheid": 0.7, "vak": [0.2, 0.6, 0.35, 0.9]}], "hoogste": 0.82,
         "breedte": 640, "hoogte": 360, "ms": 200}


def test_alleen_hond_zet_huisdier_aan_zonder_persoon(tmp_path):
    c = _coordinator(tmp_path, HOND)
    _draai(c, TUIN, _pad(time.time() - 30))
    assert c.detectie_aan(TUIN, "huisdier") and not c.detectie_aan(TUIN, "persoon")
    data = c.hass.bus.async_fire.call_args[0][1]
    assert data["soort"] == "hond" and data["aantal"] == 0 and data["hond"] is True
    assert c.personen[TUIN][0]["hond"] and c.personen[TUIN][0]["aantal"] == 0
    o = c.personen_overzicht(TUIN)
    assert o["vandaag"] == 0 and o["hond_vandaag"] == 1 and o["laatste"][0]["hond"] is True
    assert TUIN not in c.persoonsbeelden   # 'Laatste persoon' blijft een persoon


def test_persoon_met_hond(tmp_path):
    c = _coordinator(tmp_path, BEIDE)
    _draai(c, TUIN, _pad(time.time() - 30))
    assert c.detectie_aan(TUIN, "huisdier") and c.detectie_aan(TUIN, "persoon")
    data = c.hass.bus.async_fire.call_args[0][1]
    assert data["soort"] == "onbekend_persoon" and data["hond"] is True
    o = c.personen_overzicht(TUIN)
    assert o["vandaag"] == 1 and o["hond_vandaag"] == 1


def test_oude_hond_na_herstart_geen_gebeurtenis(tmp_path):
    c = _coordinator(tmp_path, HOND)
    _draai(c, TUIN, _pad(time.time() - 3 * 3600), eerste=True)
    c.hass.bus.async_fire.assert_not_called()
    assert c.herkenning[TUIN]["honden"] == 1
