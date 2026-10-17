"""v0.7.19: het livestream-token blijft gelijk als de integratie herlaadt."""
from types import SimpleNamespace as NS

from custom_components.eufy_lokaal.video import live_token_voor


def test_zelfde_token_na_herladen():
    hass = NS(data={})
    eerste = live_token_voor(hass)
    # herladen: nieuwe coördinator, zelfde hass
    assert live_token_voor(hass) == eerste
    assert len(eerste) >= 20


def test_nieuwe_run_nieuw_token():
    assert live_token_voor(NS(data={})) != live_token_voor(NS(data={}))
