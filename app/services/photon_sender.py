"""
Outbound iMessage sender for the Photon channel.

Posts to the local outbound server running inside imessage_bridge.mjs (port 8001).
The bridge holds a live spectrum-ts space per phone number and calls space.send()
on our behalf — the only way to send proactively without a real Spectrum space UUID.
"""

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


async def send_via_photon(sender_id: str, message: str) -> None:
    s = get_settings()
    port = getattr(s, "OUTBOUND_PORT", 8001)
    url = f"http://127.0.0.1:{port}/send"

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(url, json={"phone": sender_id, "text": message[:2000]})
            if resp.status_code == 404:
                logger.error(
                    "No space cached in bridge for %s — user must text first", sender_id
                )
            resp.raise_for_status()
            logger.info("Outbound delivered to %s", sender_id)
        except httpx.ConnectError:
            logger.error("Bridge outbound server not reachable — is imessage_bridge.mjs running?")
            raise
