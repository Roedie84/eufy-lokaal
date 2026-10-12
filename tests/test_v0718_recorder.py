"""v0.7.18: de diagnose-attributen gaan niet naar de recorder (~60 MB/dag)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from homeassistant.const import MATCH_ALL  # noqa: E402

from custom_components.eufy_lokaal.sensor import EufyLokaalSensor, StationP2PSensor  # noqa: E402


def test_diagnose_attributen_niet_in_de_recorder():
    for klasse in (EufyLokaalSensor, StationP2PSensor):
        assert MATCH_ALL in klasse._unrecorded_attributes, klasse.__name__
