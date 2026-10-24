"""Trigger Creao agents through the app-scoped API Trigger and poll for the result.

Shapes come from docs/creao/api-trigger.md:
  POST https://agent.creao.ai/api/v1/apps/{appId}/runs        body {"inputs": {...}}
       -> {"id": runId, "status": "pending", "appId", "appName"}
  GET  https://agent.creao.ai/api/v1/apps/{appId}/runs/{runId}
       poll until status is completed, failed or cancelled
       -> {id, status, threadId, errorCode, createdAt, startedAt, completedAt,
           result: {text, chatText, artifacts: [{id, url, type, title}]}}   (seen 2026-10-24)
  Errors: {"error": "ERROR_CODE"}
Each capi_ key only runs its own agent, so every role has its own key in .env.
We poll instead of using webhooks: Creao rejects localhost webhook URLs.

Every run spends credits. This module never retries a create and never loops runs.

Run one agent by hand:
    python backend/creao_client.py conditions --inputs '{"field": "value"}'
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import config  # noqa: F401  loads .env

BASE_URL = "https://agent.creao.ai/api/v1/apps"
USER_AGENT = "Cairn-hackathon/0.1 (ski safety demo)"

# role -> (agent id variable, capi_ key variable)
AGENTS = {
    "conditions": ("CREAO_CONDITIONS_AGENT_ID", "CREAO_CONDITIONS_API_KEY"),
    "terrain": ("CREAO_TERRAIN_AGENT_ID", "CREAO_TERRAIN_API_KEY"),
    "route": ("CREAO_ROUTE_AGENT_ID", "CREAO_ROUTE_API_KEY"),
    "incident": ("CREAO_INCIDENT_AGENT_ID", "CREAO_INCIDENT_API_KEY"),
}

TERMINAL = ("completed", "failed", "cancelled")
POLL_EVERY_S = 10
TIMEOUT_S = 30 * 60

# Raw poll responses are saved here so we can check the result format.
RUNS_LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "creao_runs"


class CreaoError(Exception):
    pass


def _agent(role):
    if role not in AGENTS:
        raise CreaoError(f"unknown role {role!r}, expected one of {list(AGENTS)}")
    id_var, key_var = AGENTS[role]
    app_id, key = os.environ.get(id_var, ""), os.environ.get(key_var, "")
    if not app_id or not key:
        raise CreaoError(f"{id_var} and {key_var} must be set in .env")
    return app_id, key


def _request(method, url, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:500]
        raise CreaoError(f"{method} {url} -> HTTP {error.code}: {detail}") from None


def create_run(role, inputs):
    """Start a run. Returns the run id. Spends credits."""
    app_id, key = _agent(role)
    response = _request("POST", f"{BASE_URL}/{app_id}/runs", key, {"inputs": inputs})
    if "id" not in response:
        raise CreaoError(f"create run response has no id: {response}")
    return response["id"]


def get_run(role, run_id):
    app_id, key = _agent(role)
    return _request("GET", f"{BASE_URL}/{app_id}/runs/{run_id}", key)


def wait_for_run(role, run_id, on_status=None):
    """Poll until the run reaches a terminal status. Returns the last raw poll response.

    on_status(status, elapsed_s) is called after every poll, for progress in the UI.
    """
    started = time.monotonic()
    last_status = None
    while True:
        run = get_run(role, run_id)
        status = run.get("status")
        elapsed = time.monotonic() - started
        if on_status and status != last_status:
            on_status(status, elapsed)
        last_status = status
        if status in TERMINAL:
            _save_raw(role, run_id, run)
            return run
        if elapsed > TIMEOUT_S:
            raise CreaoError(f"{role} run {run_id} still {status} after {TIMEOUT_S} s")
        time.sleep(POLL_EVERY_S)


def _save_raw(role, run_id, run):
    RUNS_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (RUNS_LOG_DIR / f"{stamp}-{role}-{run_id}.json").write_text(json.dumps(run, indent=1))


def result_texts(run):
    """The agent's output texts, most structured first.

    Seen on a real poll (2026-10-24): result.text holds the agent's output (bare JSON
    for the Conditions Analyst) and result.chatText holds its narration.
    """
    result = run.get("result") or {}
    texts = [t for t in (result.get("text"), result.get("chatText")) if t]
    if not texts:
        raise CreaoError(f"run has no result.text; keys: {sorted(run)}; result keys: {sorted(result)}")
    return texts


def extract_json(text):
    """Parse the agent's JSON: the whole text if it is bare JSON, else the last ```json block."""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass  # not bare JSON after all; look for a fenced block
    blocks = re.findall(r"```json\s*\n(.*?)```", text, re.DOTALL)
    if not blocks:
        raise CreaoError("no bare JSON and no ```json block in the agent's result")
    try:
        return json.loads(blocks[-1])
    except json.JSONDecodeError as error:
        raise CreaoError(f"the last ```json block is not valid JSON: {error}") from None


def output_json(run):
    """The first parseable JSON among result.text, then result.chatText."""
    errors = []
    for text in result_texts(run):
        try:
            return extract_json(text)
        except CreaoError as error:
            errors.append(str(error))
    raise CreaoError("; ".join(errors))


def server_duration_s(run):
    """Run time as Creao recorded it (createdAt to completedAt), or None if missing."""
    try:
        created = datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00"))
        completed = datetime.fromisoformat(run["completedAt"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None
    return round((completed - created).total_seconds(), 1)


def run_agent(role, inputs, on_status=None):
    """Create a run, wait for it, and return (parsed JSON output, timing dict, raw run).

    A run that completes but has no parseable JSON raises CreaoError; its raw poll
    response is already saved in data/creao_runs/ (no need to spend credits again).
    """
    started_at = datetime.now(timezone.utc)
    t0 = time.monotonic()
    run_id = create_run(role, inputs)
    if on_status:
        on_status("created", 0.0)
    run = wait_for_run(role, run_id, on_status)
    timing = {
        "role": role,
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        # Creao's own timestamps; wall time also includes up to one poll interval of waiting.
        "duration_s": server_duration_s(run),
        "wall_s": round(time.monotonic() - t0, 1),
    }
    if run.get("status") != "completed":
        raise CreaoError(f"{role} run {run_id} ended {run.get('status')}: errorCode={run.get('errorCode')}")
    return output_json(run), timing, run


def main():
    parser = argparse.ArgumentParser(description="Run one Creao agent once (spends credits).")
    parser.add_argument("role", choices=list(AGENTS))
    parser.add_argument("--inputs", required=True, help="JSON object matching the agent's input form")
    args = parser.parse_args()

    def progress(status, elapsed):
        print(f"[{elapsed:6.1f} s] {status}", flush=True)

    try:
        output, timing, run = run_agent(args.role, json.loads(args.inputs), progress)
    except CreaoError as error:
        sys.exit(f"error: {error}")
    print("\nRaw final poll response:")
    print(json.dumps(run, indent=1))
    print("\nExtracted JSON:")
    print(json.dumps(output, indent=1))
    print(f"\n{args.role} took {timing['duration_s']} s in Creao, {timing['wall_s']} s wall (run {timing['run_id']})")


if __name__ == "__main__":
    main()
