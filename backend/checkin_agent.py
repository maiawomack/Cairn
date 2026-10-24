"""The Cartesia Managed Agent that listens for the skier's reply during a crash check-in.

Mock path (no CARTESIA_CHECKIN_AGENT_ID): nothing here runs. The scripted lines are spoken by
Sonic through voice.py and the dashboard's "Skier says: I'm okay / help" buttons stand in for
the reply (main.py).

Configured path: the backend opens a WebSocket session with the agent (docs: Agent WebSocket
API, wss://api.cartesia.ai/v1/agents/websocket/{agent_id}, key in the X-API-Key header, never
in the browser) and listens for its two client tools, by name (config.CHECKIN_TOOL_OK and
CHECKIN_TOOL_HELP). There is no event for making the agent say exact words, so the scripted
lines always come from voice.py; the agent's own audio is ignored. No microphone audio is
sent yet (demo replies are buttons), so the agent only ever calls its tools if something
else gives it input.

SAFETY: Cairn never contacts real patrol or 911. After session_ready, before anything else,
the agent version's config is read and the session is refused unless it is safe:
  - config.system_tools must not enable transfer_to_number or send_dtmf (phone tools)
  - config.tools must only contain tool IDs listed in CHECKIN_ALLOWED_TOOL_IDS
    (so a webhook that sends an SMS or places a call can't be attached unnoticed)
  - an unreadable config counts as unsafe (fail closed)
unsafe_agent_config() is that rule as a pure function, tested without any API call.
"""

import asyncio
import json
import os
import urllib.error
import urllib.request

import config

PHONE_SYSTEM_TOOLS = ("transfer_to_number", "send_dtmf")


def configured():
    return bool(config.CHECKIN_AGENT_ID and os.environ.get("CARTESIA_API_KEY"))


def unsafe_agent_config(agent_config, allowed_tool_ids=None):
    """Reasons Cairn must refuse this agent (empty list = safe). Fails closed on anything unexpected."""
    allowed = config.CHECKIN_ALLOWED_TOOL_IDS if allowed_tool_ids is None else allowed_tool_ids
    if not isinstance(agent_config, dict):
        return ["the agent's config could not be read"]
    reasons = []
    system_tools = agent_config.get("system_tools") or {}
    if not isinstance(system_tools, dict):
        return ["the agent's system_tools could not be read"]
    for name in PHONE_SYSTEM_TOOLS:
        if system_tools.get(name) not in (None, False):
            reasons.append(f"system tool {name} is enabled")
    tools = agent_config.get("tools") or []
    if not isinstance(tools, list):
        return ["the agent's tools could not be read"]
    for tool in tools:
        tool_id = tool.get("id") if isinstance(tool, dict) else None
        if tool_id not in allowed:
            reasons.append(f"tool {tool_id} is not in CHECKIN_ALLOWED_TOOL_IDS")
    return reasons


def _fetch_version_config(agent_id, version_id):
    """GET the agent version's config (blocking; run in a thread). Raises on any problem."""
    request = urllib.request.Request(
        f"https://api.cartesia.ai/agents/{agent_id}/versions/{version_id}",
        headers={"X-API-Key": os.environ["CARTESIA_API_KEY"], "Cartesia-Version": config.CARTESIA_VERSION},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.load(response)
    return body.get("config") if isinstance(body, dict) else None


async def run_session(on_ok, on_help, stop, publish):
    """Listen to the check-in agent until `stop` (an asyncio.Event) is set. Returns "refused", "closed" or "error"."""
    from websockets.asyncio.client import connect  # needs cartesia[websockets], already in requirements

    url = f"wss://api.cartesia.ai/v1/agents/websocket/{config.CHECKIN_AGENT_ID}?cartesia_version={config.CARTESIA_VERSION}"
    try:
        async with connect(url, additional_headers={"X-API-Key": os.environ["CARTESIA_API_KEY"]}) as ws:
            await ws.send(json.dumps({"type": "session_create",
                                      "audio": {"input_format": "pcm_16000", "output_delivery": "speaking_pace"}}))
            checked = False
            while not stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                event = json.loads(raw)
                kind = event.get("type")
                if kind == "session_ready" and not checked:
                    checked = True
                    try:
                        agent_config = await asyncio.to_thread(
                            _fetch_version_config, config.CHECKIN_AGENT_ID, event.get("agent_version_id"))
                    except (urllib.error.URLError, ValueError, OSError):
                        agent_config = None
                    reasons = unsafe_agent_config(agent_config)
                    if reasons:
                        publish("checkin", "agent_refused", {"reasons": reasons})
                        return "refused"
                    publish("checkin", "agent_listening", {"call_id": event.get("call_id")})
                elif kind == "client_tool_call":
                    name = event.get("tool_name")
                    if name == config.CHECKIN_TOOL_OK:
                        on_ok()
                    elif name == config.CHECKIN_TOOL_HELP:
                        on_help()
                    if event.get("expects_response"):
                        await ws.send(json.dumps({"type": "client_tool_result", "tool_call_id": event["tool_call_id"],
                                                  "result": "ok" if name in (config.CHECKIN_TOOL_OK, config.CHECKIN_TOOL_HELP) else "unknown tool",
                                                  "is_error": name not in (config.CHECKIN_TOOL_OK, config.CHECKIN_TOOL_HELP)}))
                elif kind == "error":
                    publish("checkin", "agent_error", {"code": event.get("code"), "message": event.get("message")})
                    if event.get("fatal"):
                        return "error"
                # audio_output, audio_output_clear, turn_* and unknown events are ignored (the docs say to).
        return "closed"
    except Exception as error:  # a failed agent must never block the mock path or the escalation
        publish("checkin", "agent_error", {"message": str(error)[:200]})
        return "error"
