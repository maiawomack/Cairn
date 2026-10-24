"""Run the Creao chain: Conditions Analyst -> Terrain Scout -> Route Planner, then merge.

    python backend/orchestrator.py --date 2026-02-22 --mode replay

The dashboard starts it in a background thread (POST /api/agents/run). Each agent's
start, status changes, finish and output go to the dashboard log as
{ from: "creao:<role>", type, data, time } so the org chart lights up.

Every run spends credits: one chain = three agent runs. Only one chain can run at a
time, a failed agent stops the chain, and nothing is retried.
"""

import argparse
import json
import threading

import creao_client
import hazard_map

ROLES = ("conditions", "terrain", "route")

_running = threading.Lock()


def is_running():
    return _running.locked()


def _print_publish(source, type_, data=None):
    print(f"{source:20} {type_:9} {json.dumps(data or {})[:160]}", flush=True)


def _planner_notes(raw_run):
    """planner_notes from the Conditions Analyst's text JSON, when that run included it."""
    try:
        return creao_client.output_json(raw_run).get("planner_notes")
    except creao_client.CreaoError:
        return None


def run_chain(date, mode="replay", publish=_print_publish):
    """Run the three agents in order and write data/hazard_map.json. Returns the hazard map.

    Raises RuntimeError if a chain is already running.
    """
    if not _running.acquire(blocking=False):
        raise RuntimeError("a Creao chain is already running")
    try:
        publish("creao:orchestrator", "started", {"date": date, "mode": mode, "roles": list(ROLES)})
        creao_runs = []

        def run(role, inputs):
            source = f"creao:{role}"
            publish(source, "started", {})

            def on_status(status, elapsed):
                publish(source, "status", {"status": status, "elapsed_s": round(elapsed, 1)})

            try:
                output, timing, raw = creao_client.run_agent(role, inputs, on_status)
            except Exception as error:
                publish(source, "failed", {"error": str(error)})
                raise
            creao_runs.append(timing)
            publish(source, "finished", timing)
            publish(source, "output", output)
            return output, raw

        conditions, conditions_raw = run("conditions", {"mode": mode, "date": date})
        conditions_output = {"conditions": conditions, "planner_notes": _planner_notes(conditions_raw)}
        hazard_map.save_output("conditions", conditions_output)

        terrain, _ = run("terrain", {
            # The Conditions Analyst's JSON artifact, as produced.
            "conditions_json": json.dumps(conditions),
            "hazard_zones": json.dumps(hazard_map.zone_list()),
        })
        hazard_map.save_output("terrain", terrain)
        route, _ = run("route", {
            "terrain_json": json.dumps(terrain),
            "safe_exits_json": json.dumps(hazard_map.exit_list()),
            "zone_types": json.dumps(hazard_map.zone_types()),
        })
        hazard_map.save_output("route", route)

        (hazard_map.OUTPUTS_DIR / "creao_runs.json").write_text(json.dumps(creao_runs, indent=1))
        warnings = []
        result = hazard_map.build(conditions_output, terrain, route, creao_runs, warn=warnings.append)
        hazard_map.save(result)
        publish("creao:orchestrator", "finished", {
            "hazard_map": "data/hazard_map.json",
            "durations_s": {t["role"]: t["duration_s"] for t in creao_runs},
            "merge_warnings": warnings,
        })
        return result
    except Exception as error:
        publish("creao:orchestrator", "failed", {"error": str(error)})
        raise
    finally:
        _running.release()


def main():
    parser = argparse.ArgumentParser(description="Run the Creao chain once (spends credits).")
    parser.add_argument("--date", required=True)
    parser.add_argument("--mode", default="replay", choices=["replay", "live"])
    args = parser.parse_args()
    run_chain(args.date, args.mode)


if __name__ == "__main__":
    main()
