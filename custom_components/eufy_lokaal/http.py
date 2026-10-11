"""Stap 6: de livestream als MPEG-TS over HTTP (alleen met het token)."""
from __future__ import annotations

import hmac

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .const import DOMAIN


class LiveView(HomeAssistantView):
    url = f"/api/{DOMAIN}/live/{{sn}}"
    name = f"api:{DOMAIN}:live"
    requires_auth = False  # beveiligd met een willekeurig token per opstart

    def __init__(self, hass) -> None:
        self.hass = hass

    async def get(self, request: web.Request, sn: str) -> web.StreamResponse:
        from .video import stuur_ts

        coordinator = None
        for c in (self.hass.data.get(DOMAIN) or {}).values():
            if sn in getattr(c, "apparaten", {}):
                coordinator = c
                break
        token = request.query.get("token", "")
        if coordinator is None or not hmac.compare_digest(token, coordinator.live_token):
            return web.Response(status=404)
        antwoord = web.StreamResponse(headers={"Content-Type": "video/mp2t", "Cache-Control": "no-store"})
        await antwoord.prepare(request)
        try:
            await stuur_ts(coordinator.live_stream(sn), antwoord.write)
        except (ConnectionError, ConnectionResetError) as err:
            coordinator.live_stream(sn).laatste_fout = f"verbinding: {err}"
        return antwoord
