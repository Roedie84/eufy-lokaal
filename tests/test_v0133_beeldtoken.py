"""v0.13.3: persoonsbeelden met het token in het pad en een vast beeldtoken.

Ruud 24-11: melding 'Onbekend persoon gezien, Voordeur' gaf 'Bijlage kan niet worden
geladen - 404'. Het beeld stond wel in het logboek; de URL had het token als ?token=.
"""
import asyncio
from types import SimpleNamespace as NS

from custom_components.eufy_lokaal.http import PersoonView
from custom_components.eufy_lokaal.persoonslog import TOKEN_BESTAND, beeldtoken, opslaan


def test_beeldtoken_blijft_gelijk(tmp_path):
    eerste = beeldtoken(tmp_path)
    assert len(eerste) >= 20
    assert beeldtoken(tmp_path) == eerste           # na een herstart: zelfde bestand
    assert (tmp_path / TOKEN_BESTAND).exists()


class _C:
    def __init__(self, map_, live, beeld):
        self.live_token, self.beeld_token, self._map = live, beeld, map_

    def _persoonsmap(self):
        return self._map


def _view(c):
    hass = NS(data={"eufy_lokaal": {"x": c}})
    async def ex(f, *a):
        return f(*a)
    hass.async_add_executor_job = ex
    return PersoonView(hass)


def _vraag(token_query=""):
    return NS(query={"token": token_query} if token_query else {})


def test_token_in_pad_en_oude_vorm(tmp_path):
    naam = opslaan(tmp_path, "T821451025211C7C", 1795528191, 0.88, 1, b"\xff\xd8beeld")
    v = _view(_C(str(tmp_path), "live", "vast"))
    ok = asyncio.run(v.get(_vraag(), naam, token="vast"))
    assert ok.status == 200 and ok.body == b"\xff\xd8beeld"
    assert asyncio.run(v.get(_vraag(), naam, token="live")).status == 200
    assert asyncio.run(v.get(_vraag("vast"), naam)).status == 200          # oude ?token=
    assert asyncio.run(v.get(_vraag(), naam, token="fout")).status == 404
    assert asyncio.run(v.get(_vraag(), TOKEN_BESTAND, token="vast")).status == 404  # token zelf niet te lezen


def test_extra_url_heeft_token_in_pad():
    assert PersoonView.extra_urls == ["/api/eufy_lokaal/persoon/{token}/{bestand}"]
