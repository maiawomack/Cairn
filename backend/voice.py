"""Voice: speak the coordinator's line through Cartesia Sonic over WebSocket.

Audio streams from Cartesia to the backend and is relayed to the dashboard as it
arrives (voice / chunk messages, base64 PCM), so speech starts before generation
finishes and the API key never leaves the server.

Style (CLAUDE.md): cliff warnings are firm and quick, reroutes are calm. A cliff
warning line like "Stop now. Cliff ahead. Exit to Rock Alley." is pushed in two parts
on one context: the alert sentences firm, the final reroute sentence calm.

If Cartesia is not configured or fails, a voice / unavailable or error message tells
the dashboard to speak the line with the browser's own speech, so a warning is never silent.
"""

import base64
import os
import re
import time
import uuid

import cartesia

import config

_client = None


def _cartesia():
    global _client
    if _client is None:
        _client = cartesia.AsyncCartesia(
            api_key=os.environ["CARTESIA_API_KEY"],
            default_headers={"cartesia-version": config.CARTESIA_VERSION},
        )
    return _client


def style_parts(line, zone_type, level):
    """Split a line into (text, style) parts. Cliff warnings: alert firm, final reroute sentence calm."""
    if zone_type != "cliff" or level != "warning":
        return [(line, "calm")]
    sentences = re.findall(r"[^.!?]+[.!?]*", line)
    sentences = [s.strip() for s in sentences if s.strip()]
    if len(sentences) < 2:
        return [(line, "firm")]
    return [(" ".join(sentences[:-1]) + " ", "firm"), (sentences[-1], "calm")]


async def speak(line, zone_type, level, publish):
    """Stream one line to the dashboard. Returns a summary dict (also published as voice / spoken)."""
    utterance = uuid.uuid4().hex[:8]
    parts = style_parts(line, zone_type, level)
    base = {"utterance": utterance, "line": line, "styles": [s for _, s in parts]}

    missing = [v for v in ("CARTESIA_API_KEY", "CARTESIA_VOICE_ID") if not os.environ.get(v)]
    if missing:
        summary = {**base, "status": "unavailable", "reason": f"{', '.join(missing)} not set"}
        publish("voice", "unavailable", summary)
        return summary

    publish("voice", "start", {**base, "sample_rate": config.VOICE_SAMPLE_RATE,
                               "encoding": config.VOICE_ENCODING}, history=False)
    started = time.monotonic()
    first_audio_ms = None
    audio_bytes = 0
    try:
        async with _cartesia().tts.websocket_connect() as ws:
            ctx = ws.context(
                model_id=config.CARTESIA_TTS_MODEL,
                voice=os.environ["CARTESIA_VOICE_ID"],
                output_format={"container": "raw", "encoding": config.VOICE_ENCODING,
                               "sample_rate": config.VOICE_SAMPLE_RATE},
                language="en",
            )
            for text, style in parts:
                await ctx.push(text, generation_config=config.VOICE_STYLES[style])
            await ctx.no_more_inputs()

            seq = 0
            async for response in ctx.receive():
                if response.type == "chunk" and response.audio:
                    if first_audio_ms is None:
                        first_audio_ms = round((time.monotonic() - started) * 1000)
                    audio_bytes += len(response.audio)
                    publish("voice", "chunk", {"utterance": utterance, "seq": seq,
                                               "audio": base64.b64encode(response.audio).decode()},
                            history=False)
                    seq += 1
                elif response.type == "error":
                    raise RuntimeError(response.message or response.title or "Cartesia error")
                if response.done:
                    break
    except Exception as error:  # any failure: the dashboard falls back to browser speech
        summary = {**base, "status": "error", "reason": str(error)[:200]}
        publish("voice", "error", summary)
        return summary

    seconds = audio_bytes / 2 / config.VOICE_SAMPLE_RATE  # 16 bit mono
    summary = {**base, "status": "spoken", "first_audio_ms": first_audio_ms,
               "total_ms": round((time.monotonic() - started) * 1000), "audio_s": round(seconds, 2)}
    publish("voice", "end", {"utterance": utterance}, history=False)
    publish("voice", "spoken", summary)
    return summary
