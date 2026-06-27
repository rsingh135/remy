"""
Outbound message sender for the Photon iMessage channel.

Looks up the recipient's Photon space_id from Redis (stored at webhook receipt),
then POSTs to the Spectrum REST API to deliver the reply.

The space_id is the stable identifier for a conversation thread in Photon.
It is cached with a 24-hour TTL that is refreshed on every inbound webhook,
so active users will never see a stale lookup.
"""

import logging

import httpx
import redis as redis_lib

from app.config import get_settings

logger = logging.getLogger(__name__)

_SPECTRUM_BASE = "https://spectrum.photon.codes"


async def send_via_photon(sender_id: str, message: str) -> None:
    s = get_settings()
    r = redis_lib.from_url(s.REDIS_URL, decode_responses=True)
    space_id = r.get(f"photon_space:{sender_id}")

    if not space_id:
        logger.error(
            "No Photon space_id cached for sender %s — cannot deliver reply", sender_id
        )
        return

    url = f"{_SPECTRUM_BASE}/projects/{s.PHOTON_PROJECT_ID}/spaces/{space_id}/messages"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            url,
            json={"content": {"type": "text", "text": message[:2000]}},
            auth=(s.PHOTON_PROJECT_ID, s.PHOTON_PROJECT_SECRET),
        )
        if resp.status_code >= 400:
            logger.error(
                "Photon send failed for space %s: %s %s",
                space_id,
                resp.status_code,
                resp.text,
            )
        else:
            logger.info("Photon reply delivered to space %s", space_id)
