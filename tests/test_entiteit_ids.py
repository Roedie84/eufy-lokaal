"""Eenmalig herstel van entiteit-ID's: geen dubbele ruimtenaam."""
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

from custom_components.eufy_lokaal import _herstel_entiteit_ids


def test_dubbele_ruimtenaam_valt_weg():
    entries = [NS(entity_id="sensor.zolder_zolder_batterij", device_id="d1"),
               NS(entity_id="binary_sensor.achtertuin_achtertuin_beweging_2", device_id="d2"),
               NS(entity_id="camera.zolder_zolder", device_id="d1"),
               NS(entity_id="sensor.voortuin_deurbel_batterij", device_id="d3")]
    bezet = {e.entity_id for e in entries} | {"binary_sensor.achtertuin_achtertuin_beweging"}
    reg = MagicMock()
    reg.async_get.side_effect = lambda i: object() if i in bezet else None
    del reg.async_regenerate_entity_id
    apparaten = {"d1": NS(name="Zolder", name_by_user=None, area_id="zolder"),
                 "d2": NS(name="Achtertuin", name_by_user=None, area_id="achtertuin"),
                 "d3": NS(name="Deurbel", name_by_user=None, area_id="voortuin")}
    ruimtes = {"zolder": NS(name="Zolder"), "achtertuin": NS(name="Achtertuin"), "voortuin": NS(name="Voortuin")}
    entry = NS(data={"entiteit_ids": 1}, entry_id="x")
    hass = MagicMock()
    with patch("homeassistant.helpers.entity_registry.async_get", return_value=reg), \
         patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=entries), \
         patch("homeassistant.helpers.device_registry.async_get", return_value=MagicMock(async_get=apparaten.get)), \
         patch("homeassistant.helpers.area_registry.async_get", return_value=MagicMock(async_get_area=ruimtes.get)):
        _herstel_entiteit_ids(hass, entry)
    hernoemd = {c.args[0]: c.kwargs["new_entity_id"] for c in reg.async_update_entity.call_args_list}
    assert hernoemd == {"sensor.zolder_zolder_batterij": "sensor.zolder_batterij",
                        "binary_sensor.achtertuin_achtertuin_beweging_2": "binary_sensor.achtertuin_beweging",
                        "camera.zolder_zolder": "camera.zolder"}
    assert hass.config_entries.async_update_entry.call_args.kwargs["data"]["entiteit_ids"] == 2
