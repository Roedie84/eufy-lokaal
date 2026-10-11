"""Het inloggen tegen een nagebootste Eufy-cloud: gewone login, e-mailcode,
captcha, fout wachtwoord, en versleutelde lijsten."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

MAP = Path(__file__).parents[1] / "custom_components" / "eufy_lokaal"
spec = importlib.util.spec_from_file_location("eufy_cloud_t", MAP / "cloud.py")
cloud = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cloud
spec.loader.exec_module(cloud)

SERVER_PRIVE = "2a" * 32  # de nep-server heeft zijn eigen sessiesleutel


class _Antwoord:
    def __init__(self, data, status=200):
        self._data, self.status = data, status

    async def json(self, content_type=None):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _NepCloud:
    """Gedraagt zich als de Eufy-API voor de paar gebruikte paden."""

    def __init__(self, scenario):
        self.scenario = list(scenario)
        self.verzoeken = []

    def get(self, url):
        return _Antwoord({"code": 0, "data": {"domain": "security-app-eu.eufylife.com"}})

    def post(self, url, json=None, headers=None):
        pad = url.split(".com/", 1)[1]
        self.verzoeken.append((pad, json, headers))
        if pad == "v2/passport/login_sec":
            # het wachtwoord moet met het gedeelde geheim te ontsleutelen zijn
            geheim = cloud.gedeeld_geheim(SERVER_PRIVE_VOOR_CLIENT, json["client_secret_info"]["public_key"])
            assert cloud.ontsleutel(json["password"], geheim) == "geheim!"
            return _Antwoord(self.scenario.pop(0))
        if pad in ("v2/house/station_list", "v2/house/device_list"):
            assert headers["X-Auth-Token"] == "tok"
            geheim = cloud.gedeeld_geheim(SERVER_PRIVE, json_client_publiek[0])
            lijst = [{"station_sn": "T8030P1", "p2p_did": "EUPRAKM-012345-ABCDE"}]
            return _Antwoord({"code": 0, "data": cloud.versleutel(json_dump(lijst), geheim)})
        return _Antwoord({"code": 0, "data": {}})


json_dump = json.dumps
json_client_publiek = []
# De vaste serversleutel hoort bij een geheime sleutel die wij niet kennen;
# daarom rekent de nep-server het geheim vanaf de client-kant na: hij krijgt
# de prive-sleutel van de client mee via deze variabele.
SERVER_PRIVE_VOOR_CLIENT = None


def _maak(scenario):
    global SERVER_PRIVE_VOOR_CLIENT
    web = _NepCloud(scenario)
    c = cloud.EufyCloud(web, "nl", "test@example.com", "geheim!")
    SERVER_PRIVE_VOOR_CLIENT = c.sessie.prive_sleutel
    # het "publiek" dat de nep-server gebruikt is de vaste serversleutel:
    # gedeeld_geheim(client_prive, SERVER_PUBLIEK) == gedeeld_geheim(?, client_publiek)
    return web, c


def _ok():
    publiek = cloud.publieke_sleutel_hex(SERVER_PRIVE)
    return {"code": 0, "data": {"user_id": "u1", "auth_token": "tok", "token_expires_at": 4102444800,
                                "server_secret_info": {"public_key": publiek}}}


def test_gewone_login_en_lijsten(monkeypatch):
    web, c = _maak([_ok()])
    # het wachtwoord is versleuteld met (client_prive, vaste serversleutel)
    monkeypatch.setattr(cloud, "gedeeld_geheim", _geheim_hulp(cloud.gedeeld_geheim, c))
    asyncio.run(c.inloggen())
    assert c.sessie.token == "tok" and c.sessie.geldig and c.sessie.user_id == "u1"
    json_client_publiek[:] = [cloud.publieke_sleutel_hex(c.sessie.prive_sleutel)]
    stations = asyncio.run(c.stations())
    assert stations == [{"station_sn": "T8030P1", "p2p_did": "EUPRAKM-012345-ABCDE"}]
    koppen = web.verzoeken[-1][2]
    assert koppen["gtoken"] == "a" * 0 + __import__("hashlib").md5(b"u1").hexdigest()
    assert koppen["Country"] == "NL"


def test_e_mailcode_gevraagd(monkeypatch):
    web, c = _maak([{"code": 26052, "data": {"auth_token": "tijdelijk"}}])
    monkeypatch.setattr(cloud, "gedeeld_geheim", _geheim_hulp(cloud.gedeeld_geheim, c))
    try:
        asyncio.run(c.inloggen())
    except cloud.EMailCodeNodig:
        pass
    else:
        raise AssertionError
    assert web.verzoeken[-1][0] == "v1/sms/send/verify_code"
    assert web.verzoeken[-1][1]["message_type"] == 2


def test_captcha_gevraagd(monkeypatch):
    web, c = _maak([{"code": 100032, "data": {"captcha_id": "c1", "item": "data:image/png;base64,AAAA"}}])
    monkeypatch.setattr(cloud, "gedeeld_geheim", _geheim_hulp(cloud.gedeeld_geheim, c))
    try:
        asyncio.run(c.inloggen())
    except cloud.CaptchaNodig as e:
        assert e.captcha_id == "c1" and e.afbeelding.startswith("data:image/png")
    else:
        raise AssertionError


def test_fout_wachtwoord(monkeypatch):
    web, c = _maak([{"code": 26006, "msg": "wrong", "data": {}}])
    monkeypatch.setattr(cloud, "gedeeld_geheim", _geheim_hulp(cloud.gedeeld_geheim, c))
    try:
        asyncio.run(c.inloggen())
    except cloud.CloudFout as e:
        assert e.code == 26006 and not isinstance(e, cloud.EMailCodeNodig)
    else:
        raise AssertionError


def _geheim_hulp(echt, c):
    """De nep-server kent de geheime serversleutel van Eufy niet; hij rekent
    het geheim daarom aan de clientkant uit (zelfde getal, ECDH is symmetrisch)."""
    def hulp(prive_hex, publiek_hex):
        if prive_hex is None:
            return echt(c.sessie.prive_sleutel, cloud.SERVER_PUBLIEKE_SLEUTEL)
        if prive_hex == c.sessie.prive_sleutel or prive_hex == SERVER_PRIVE_VOOR_CLIENT:
            if publiek_hex.startswith("04") and publiek_hex == cloud.publieke_sleutel_hex(c.sessie.prive_sleutel):
                return echt(c.sessie.prive_sleutel, cloud.SERVER_PUBLIEKE_SLEUTEL)
        return echt(prive_hex, publiek_hex)
    return hulp
