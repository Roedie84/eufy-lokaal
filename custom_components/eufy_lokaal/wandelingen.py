"""v0.12.0: wandelingen met de hond, afgeleid uit het logboek van de camera's.

Ruud 19-11: ze vertrekken meestal via de achtertuin. De camera ziet de hond met iemand
vertrekken, daarna een tijd niets, en dan de hond terug. Zo wordt het geteld:

- 'passage': opeenvolgende waarnemingen met een hond, steeds hooguit SAMEN_S uit elkaar;
- 'wandeling': een passage met minstens één persoon, gevolgd door de volgende passage
  na WEG_MIN_S tot WEG_MAX_S; vertrek = eind van de eerste passage, terug = begin van de
  tweede. Wie: de herkende namen van beide passages samen (bij vertrek staat men vaak met
  de rug naar de camera, bij terugkomst niet);
- 'onderweg': de laatste passage had een persoon, is WEG_MIN_S tot WEG_MAX_S geleden en
  er is nog geen hond teruggezien.

Een afleiding, geen meting: speelt de hond in de tuin, gaat hij naar binnen en komt hij
later terug in de tuin, dan lijkt dat ook een wandeling. Daarom 'waarschijnlijk'."""
from __future__ import annotations

SAMEN_S = 3 * 60        # waarnemingen hooguit 3 min uit elkaar horen bij dezelfde passage
WEG_MIN_S = 4 * 60      # Ruud 19-11: bij regen soms maar 5 minuten
WEG_MAX_S = 3 * 3600


def passages(momenten: list[dict]) -> list[dict]:
    """Waarnemingen met hond (alle camera's) -> passages, oudste eerst."""
    met_hond = sorted((m for m in momenten if m.get("hond")), key=lambda m: m["tijd"])
    uit: list[dict] = []
    for m in met_hond:
        if uit and m["tijd"] - uit[-1]["eind"] <= SAMEN_S:
            p = uit[-1]
            p["eind"] = m["tijd"]
        else:
            p = {"begin": m["tijd"], "eind": m["tijd"], "persoon": False, "namen": []}
            uit.append(p)
        if m.get("aantal"):
            p["persoon"] = True
        for n in m.get("namen") or []:
            if n not in p["namen"]:
                p["namen"].append(n)
    return uit


def wandelingen(momenten: list[dict], nu: float) -> dict:
    """{'wandelingen': [...] (nieuwste eerst), 'onderweg': {...} of None}."""
    ps = passages(momenten)
    lopen = []
    terugkomst = set()          # een passage die een terugkomst is, is geen nieuw vertrek
    for i, (a, b) in enumerate(zip(ps, ps[1:])):
        weg = b["begin"] - a["eind"]
        if i not in terugkomst and a["persoon"] and WEG_MIN_S <= weg <= WEG_MAX_S:
            namen = list(a["namen"]) + [n for n in b["namen"] if n not in a["namen"]]
            lopen.append({"vertrek": a["eind"], "terug": b["begin"], "duur_min": round(weg / 60),
                          "namen": namen})
            terugkomst.add(i + 1)
    onderweg = None
    if ps and (len(ps) - 1) not in terugkomst and ps[-1]["persoon"] and WEG_MIN_S <= nu - ps[-1]["eind"] <= WEG_MAX_S:
        onderweg = {"sinds": ps[-1]["eind"], "namen": list(ps[-1]["namen"]),
                    "minuten": round((nu - ps[-1]["eind"]) / 60)}
    return {"wandelingen": lopen[::-1], "onderweg": onderweg}


def per_persoon(lopen: list[dict]) -> dict[str, int]:
    """Hoe vaak wie meeliep (onbekend telt als 'onbekend')."""
    uit: dict[str, int] = {}
    for w in lopen:
        for n in w["namen"] or ["onbekend"]:
            uit[n] = uit.get(n, 0) + 1
    return dict(sorted(uit.items(), key=lambda kv: -kv[1]))
