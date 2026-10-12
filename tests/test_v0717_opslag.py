"""v0.7.17: de live momentopname overleeft een herstart."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from custom_components.eufy_lokaal.opslag import (  # noqa: E402
    bestanden,
    momentopname_laden,
    momentopname_opslaan,
)

JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100 + b"\xff\xd9"


def test_opslaan_en_laden(tmp_path):
    map_ = tmp_path / "eufy_lokaal"
    momentopname_opslaan(map_, "T8214ABC", {"data": JPEG, "tijd": 1791700000.5, "duur_s": 3.2,
                                            "reden": "aangebeld", "grootte": len(JPEG), "fout": None})
    m = momentopname_laden(map_, "T8214ABC")
    assert m["data"] == JPEG
    assert m["tijd"] == 1791700000.5
    assert m["reden"] == "aangebeld" and m["duur_s"] == 3.2
    assert m["grootte"] == len(JPEG) and m["fout"] is None and m["bewaard"] is True
    assert not any(p.suffix == ".tmp" for p in map_.iterdir())


def test_niets_bewaard(tmp_path):
    assert momentopname_laden(tmp_path, "T8214ABC") is None


def test_geen_beeld_wordt_niet_bewaard(tmp_path):
    momentopname_opslaan(tmp_path / "m", "X", {"data": None, "fout": "time-out"})
    assert not (tmp_path / "m").exists()


def test_kapot_bestand_wordt_genegeerd(tmp_path):
    jpg, _ = bestanden(tmp_path, "X")
    jpg.write_bytes(b"geen jpeg")
    assert momentopname_laden(tmp_path, "X") is None


def test_zonder_gegevens_tijd_van_het_bestand(tmp_path):
    jpg, meta = bestanden(tmp_path, "X")
    jpg.write_bytes(JPEG)
    m = momentopname_laden(tmp_path, "X")
    assert m["data"] == JPEG and m["tijd"] == jpg.stat().st_mtime and m["reden"] is None
    meta.write_text("{kapot")
    assert momentopname_laden(tmp_path, "X")["data"] == JPEG
    meta.write_text(json.dumps([1, 2]))
    assert momentopname_laden(tmp_path, "X")["data"] == JPEG


def test_serienummer_wordt_veilige_bestandsnaam(tmp_path):
    jpg, meta = bestanden(tmp_path, "../T82/14")
    assert jpg.parent == tmp_path and meta.parent == tmp_path
    assert "/" not in jpg.name and ".." not in jpg.stem.replace("momentopname_", "")[:2]


def _coordinator(tmp_path):
    import asyncio
    from types import SimpleNamespace as NS

    from custom_components.eufy_lokaal.coordinator import EufyLokaalCoordinator

    async def executor(fn, *args):
        return fn(*args)

    c = object.__new__(EufyLokaalCoordinator)
    c.hass = NS(config=NS(path=lambda *d: str(tmp_path.joinpath(*d))), async_add_executor_job=executor)
    c.apparaten = {"T8214ABC": {}, "T8030HB": {}}
    c.stations = {"T8030HB": {}}
    c.momentopnamen = {}
    return c, asyncio.run


def test_coordinator_bewaart_en_zet_terug_na_herstart(tmp_path):
    c, run = _coordinator(tmp_path)
    c.momentopnamen["T8214ABC"] = {"data": JPEG, "tijd": 1791700000.0, "duur_s": 2.0,
                                   "reden": "aangebeld", "grootte": len(JPEG), "fout": None}
    run(c._async_momentopname_bewaren("T8214ABC"))
    assert (tmp_path / ".storage" / "eufy_lokaal" / "momentopname_T8214ABC.jpg").exists()

    nieuw, run = _coordinator(tmp_path)  # na de herstart: geheugen leeg
    run(nieuw.async_momentopnamen_laden())
    assert nieuw.momentopnamen["T8214ABC"]["data"] == JPEG
    assert nieuw.momentopnamen["T8214ABC"]["reden"] == "aangebeld"
    assert "T8030HB" not in nieuw.momentopnamen  # de HomeBase zelf heeft geen beeld
