/**
 * iMessage bridge for Remy.
 *
 * Receives iMessages via Photon/spectrum-ts and calls the FastAPI app's
 * internal endpoint to get a reply, then sends it back via space.send().
 *
 * Also runs a local HTTP server on port 8001 so Celery tasks (reminders,
 * nightly check-ins) can send proactive messages without going through
 * the Spectrum REST API (which does not accept the SDK space ID format).
 *
 * Run with: node imessage_bridge.mjs
 * Requires: PHOTON_PROJECT_ID and PHOTON_PROJECT_SECRET in .env
 */

import { createServer } from "http";
import { Spectrum } from "spectrum-ts";
import { imessage } from "spectrum-ts/providers/imessage";
import { readFileSync } from "fs";

// Load .env manually (no dotenv dependency needed)
function loadEnv() {
  try {
    const lines = readFileSync(".env", "utf8").split("\n");
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eq = trimmed.indexOf("=");
      if (eq === -1) continue;
      const key = trimmed.slice(0, eq).trim();
      const val = trimmed.slice(eq + 1).trim();
      if (!process.env[key]) process.env[key] = val;
    }
  } catch {
    // .env not found — rely on actual environment variables
  }
}

loadEnv();

const FASTAPI_URL = process.env.BASE_URL || "http://localhost:8000";
const OUTBOUND_PORT = parseInt(process.env.OUTBOUND_PORT || "8001", 10);
const PROJECT_ID = process.env.PHOTON_PROJECT_ID;
const PROJECT_SECRET = process.env.PHOTON_PROJECT_SECRET;

if (!PROJECT_ID || !PROJECT_SECRET) {
  console.error("Missing PHOTON_PROJECT_ID or PHOTON_PROJECT_SECRET in .env");
  process.exit(1);
}

// phone -> space map so proactive senders can reach any known user
const spaceRegistry = new Map();

// Local HTTP server: POST /send { phone, text } → space.send(text)
const outboundServer = createServer((req, res) => {
  if (req.method !== "POST" || req.url !== "/send") {
    res.writeHead(404);
    res.end();
    return;
  }
  let body = "";
  req.on("data", (chunk) => { body += chunk; });
  req.on("end", async () => {
    try {
      const { phone, text } = JSON.parse(body);
      const space = spaceRegistry.get(phone);
      if (!space) {
        console.error(`Outbound: no space cached for ${phone}`);
        res.writeHead(404, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "No space found for phone" }));
        return;
      }
      await space.send(text);
      console.log(`Outbound delivered to ${phone}: ${text}`);
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true }));
    } catch (err) {
      console.error("Outbound send error:", err.message);
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: err.message }));
    }
  });
});

outboundServer.listen(OUTBOUND_PORT, "127.0.0.1", () => {
  console.log(`Outbound server ready on port ${OUTBOUND_PORT}`);
});

console.log(`Connecting to Photon project ${PROJECT_ID}...`);

const app = await Spectrum({
  projectId: PROJECT_ID,
  projectSecret: PROJECT_SECRET,
  providers: [imessage.config()],
});

console.log("Bridge ready — waiting for iMessages...");

for await (const [space, message] of app.messages) {
  if (message.content.type !== "text") continue;
  if (message.direction !== "inbound") continue;

  const senderId = message.sender?.id;
  const text = message.content.text;

  // Keep space fresh so proactive messages can reach this user
  if (senderId) spaceRegistry.set(senderId, space);

  console.log(`[${message.platform}] ${senderId}: ${text}`);

  try {
    const resp = await fetch(`${FASTAPI_URL}/sms/photon/internal`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sender_id: senderId, message_text: text }),
      signal: AbortSignal.timeout(25000),
    });

    if (!resp.ok) {
      console.error(`FastAPI error: ${resp.status} ${await resp.text()}`);
      continue;
    }

    const data = await resp.json();
    const replies = data.replies || (data.reply ? [data.reply] : []);
    for (const part of replies) {
      if (!part) continue;
      await space.send(part);
      console.log(`Replied to ${senderId}: ${part}`);
      if (replies.length > 1) await new Promise(r => setTimeout(r, 800));
    }
  } catch (err) {
    console.error("Bridge error:", err.message);
  }
}
