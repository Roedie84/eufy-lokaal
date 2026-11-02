"""v0.7.21 - parametersensor in °C heeft device_class temperature."""
from pathlib import Path

BRON = (Path(__file__).parent.parent / "custom_components/eufy_lokaal/sensor.py").read_text()


def test_temperatuurklasse_bij_graden():
    assert 'if self._attr_native_unit_of_measurement == "°C":' in BRON
    assert "SensorDeviceClass.TEMPERATURE" in BRON


def test_geen_tekst_met_device_class():
    assert 'if getattr(self, "_attr_device_class", None) is not None:' in BRON
