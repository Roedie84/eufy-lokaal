"""v0.12.0: wandelingen met de hond uit het logboek."""
from custom_components.eufy_lokaal import wandelingen as w

T = 1795100000


def m(min_, hond=True, aantal=1, namen=()):
    return {"tijd": T + min_ * 60, "hond": hond, "aantal": aantal, "namen": list(namen)}


def test_vertrek_en_terug_is_een_wandeling_met_namen_van_beide_kanten():
    momenten = [m(0, namen=()), m(2, aantal=1), m(55, namen=["Ruud"]), m(56, aantal=0)]
    uit = w.wandelingen(momenten, T + 3600 * 2)
    assert uit["wandelingen"] == [{"vertrek": T + 120, "terug": T + 55 * 60, "duur_min": 53, "namen": ["Ruud"]}]
    assert uit["onderweg"] is None


def test_hond_alleen_in_de_tuin_is_geen_wandeling():
    momenten = [m(0, aantal=0), m(40, aantal=0)]
    assert w.wandelingen(momenten, T + 7200)["wandelingen"] == []


def test_kort_telt_ook_maar_te_kort_of_te_lang_niet():
    assert w.wandelingen([m(0), m(2)], T + 7200)["wandelingen"] == []         # zelfde passage
    assert w.wandelingen([m(0), m(3.5)], T + 7200)["wandelingen"] == []       # 3,5 min: te kort
    assert w.wandelingen([m(0), m(5)], T + 7200)["wandelingen"][0]["duur_min"] == 5   # regen: 5 minuten
    assert len(w.wandelingen([m(0), m(30)], T + 7200)["wandelingen"]) == 1
    assert w.wandelingen([m(0), m(200)], T + 86400)["wandelingen"] == []      # > 3 uur


def test_terugkomst_is_geen_nieuw_vertrek():
    # vertrek 0, terug 40; de terugkomst op 40 is geen nieuw vertrek, dus 80 is een nieuwe passage
    uit = w.wandelingen([m(0), m(40), m(80)], T + 4 * 3600)
    assert [(x["vertrek"] - T) // 60 for x in uit["wandelingen"]] == [0]
    # na de terugkomst is er niemand 'onderweg'
    assert w.wandelingen([m(0), m(40)], T + 60 * 60)["onderweg"] is None



def test_onderweg():
    uit = w.wandelingen([m(0, namen=["Jill"])], T + 20 * 60)
    assert uit["onderweg"] == {"sinds": T, "namen": ["Jill"], "minuten": 20}
    assert w.wandelingen([m(0)], T + 2 * 60)["onderweg"] is None             # nog in de tuin
    assert w.wandelingen([m(0)], T + 4 * 3600)["onderweg"] is None           # te lang: niet meer 'onderweg'
    assert w.wandelingen([m(0, aantal=0)], T + 20 * 60)["onderweg"] is None  # hond zonder persoon


def test_passages_samenvoegen_en_zonder_hond_overslaan():
    ps = w.passages([m(0), m(1, hond=False), m(2, namen=["Ruud"]), m(3, namen=["Ruud", "Jill"])])
    assert len(ps) == 1 and ps[0]["namen"] == ["Ruud", "Jill"] and ps[0]["eind"] == T + 3 * 60


def test_per_persoon():
    lopen = [{"namen": ["Ruud"]}, {"namen": ["Ruud", "Jill"]}, {"namen": []}]
    assert w.per_persoon(lopen) == {"Ruud": 2, "Jill": 1, "onbekend": 1}


def test_overzicht_in_de_coordinator(tmp_path):
    from test_v090_gezichten import TUIN, _coordinator

    c = _coordinator(tmp_path, live=None)
    import time
    nu = time.time()
    c.personen[TUIN] = [{"tijd": nu - 3000, "hond": True, "aantal": 1, "namen": []},
                        {"tijd": nu - 600, "hond": True, "aantal": 1, "namen": ["Majorie"]}]
    o = c.wandel_overzicht()
    assert len(o["laatste"]) == 1 and o["laatste"][0]["namen"] == ["Majorie"]
    assert o["per_persoon_14_dagen"] == {"Majorie": 1}
