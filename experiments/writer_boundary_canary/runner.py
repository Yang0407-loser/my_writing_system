from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from app.utils.llm_client import estimate_tokens, get_llm_client
from experiments.style_control.dedupe import inspect_overlap

from .aggregate import aggregate_reviews
from .anonymise import anonymise
from .boundary_ticket import build_ticket, mock_ticket, validate_ticket
from .models import BoundaryTicket, CompiledSummary
from .prompts import boundary_prompt, w0_prompt, w2_prompt
from .review import instructions
from .summary_compiler import compile_summary, validate_summary

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = ROOT / "experiments/writer_boundary_canary/fixtures/writer_boundary_canary_sc4.json"
DEFAULT_OUTPUT = ROOT / "outputs/writer-boundary-canary-v1-1"
DEFAULT_REAL_OUTPUT = ROOT / "outputs/writer-boundary-canary-v1-1-real"
REFERENCE = ROOT / "experiments/style_control/fixtures/reference_dialogue_noir.txt"
WRITER_FREEDOMS = ["opening_order", "focus_selection", "dialogue_structure", "emotion_channels", "paragraph_topology", "ending_expression", "local_asymmetry", "character_specific_diction"]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_plan(fixture_path: Path = DEFAULT_FIXTURE, output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    fixture = read_json(fixture_path); calls = []
    for repeat in (1, 2):
        seed = fixture["experiment"]["base_seed"] + repeat
        calls += [
            {"call_id": f"SC4__W0__r{repeat}:prose", "arm": "W0", "repeat": repeat, "stage": "prose", "seed": seed, "status": "pending", "result_path": str(output_dir / "results" / f"SC4__W0__r{repeat}.json")},
            {"call_id": f"SC4__W2__r{repeat}:ticket", "arm": "W2", "repeat": repeat, "stage": "ticket", "seed": seed, "status": "pending", "result_path": str(output_dir / "tickets" / f"SC4__W2__r{repeat}.json")},
            {"call_id": f"SC4__W2__r{repeat}:prose", "arm": "W2", "repeat": repeat, "stage": "prose", "seed": seed, "status": "pending", "result_path": str(output_dir / "results" / f"SC4__W2__r{repeat}.json")},
        ]
    plan = {
        "schema_version": "1.1", "scene_id": "SC4", "fixture_path": str(fixture_path),
        "real_calls_enabled_by_fixture": False, "planned_call_count": 6,
        "planned_text_count": 4, "planned_ticket_count": 2, "planned_summary_count": 2,
        "pair_count": 2, "writer_freedoms_private_metadata_only": WRITER_FREEDOMS, "calls": calls,
    }
    write_json(output_dir / "boundary-canary-plan.json", plan); return plan


def estimate_budget(fixture_path: Path = DEFAULT_FIXTURE) -> dict[str, Any]:
    f = read_json(fixture_path)
    ticket_in = sum(estimate_tokens(boundary_prompt(f, r)) for r in (1, 2))
    w0_in = 2 * estimate_tokens(w0_prompt(f))
    w2_in = sum(estimate_tokens(w2_prompt(f, compile_summary(mock_ticket(f, r)))) for r in (1, 2))
    return {
        "real_call_count": 6, "boundary_maker_calls": 2, "prose_calls": 4,
        "boundary_maker_input_tokens": ticket_in, "w0_prose_input_tokens": w0_in,
        "w2_prose_input_tokens": w2_in, "estimated_input_tokens": ticket_in + w0_in + w2_in,
        "max_output_tokens": 2 * f["boundary_maker"]["max_tokens"] + 4 * f["prose"]["max_tokens"],
        "target_prose_chars_total": 4 * f["prose"]["target_chars"], "summary_compiler_model_tokens": 0,
    }


def _usage(prompt: str, text: str, start: float, meta: dict[str, Any]) -> dict[str, Any]:
    return {"input_tokens": int(meta.get("input_tokens", estimate_tokens(prompt))), "output_tokens": int(meta.get("output_tokens", estimate_tokens(text))), "latency_ms": int(float(meta.get("latency_seconds", time.perf_counter() - start)) * 1000), "finish_reason": str(meta.get("finish_reason", "unknown"))}


def _ticket_from_file(path: Path, fixture: dict[str, Any]) -> BoundaryTicket:
    raw = read_json(path)
    return validate_ticket(BoundaryTicket.model_validate({k: raw[k] for k in BoundaryTicket.model_fields}), fixture)


def _summary_from_file(path: Path, ticket: BoundaryTicket) -> CompiledSummary:
    raw = read_json(path)
    return validate_summary(CompiledSummary.model_validate({k: raw[k] for k in CompiledSummary.model_fields}), ticket)


def _copy(text: str, meta: dict[str, Any], backend: str) -> dict[str, Any]:
    if backend == "mock":
        return {"evaluated": False, "truncation": False, "exact_sentence_copy": False, "shared_12gram": False, "reference_routed_to_prompt": False}
    metrics = inspect_overlap(text, REFERENCE.read_text(encoding="utf-8"))
    return {"evaluated": True, "truncation": meta.get("finish_reason") == "length", "exact_sentence_copy": metrics["exact_copied_sentence_count"] > 0, "shared_12gram": metrics["shared_12gram_unique_count"] > 0, "reference_routed_to_prompt": False, "metrics": metrics}


def run(fixture_path: Path = DEFAULT_FIXTURE, output_dir: Path = DEFAULT_OUTPUT, *, backend: str, allow_real_calls: bool = False, rerun_id: str | None = None) -> dict[str, Any]:
    if backend == "llm" and not allow_real_calls:
        raise PermissionError("real calls disabled; explicit --enable-real-calls required")
    fixture = read_json(fixture_path); plan_path = output_dir / "boundary-canary-plan.json"
    plan = read_json(plan_path) if plan_path.exists() else build_plan(fixture_path, output_dir)
    llm = get_llm_client() if backend == "llm" else None; completed = 0
    for call in plan["calls"]:
        if rerun_id and call["call_id"] != rerun_id: continue
        path = Path(call["result_path"])
        if path.exists():
            old = read_json(path)
            if rerun_id and old.get("status") != "failed": raise ValueError("rerun-id only accepts failed call")
            if not rerun_id and old.get("status") in {"completed", "mock_completed"}:
                if call["stage"] == "ticket":
                    ticket = _ticket_from_file(path, fixture)
                    _summary_from_file(output_dir / "summaries" / f"SC4__W2__r{call['repeat']}.json", ticket)
                call["status"] = old["status"]; completed += 1; continue
        repeat, arm, stage = call["repeat"], call["arm"], call["stage"]
        start = time.perf_counter(); meta: dict[str, Any] = {}
        try:
            if stage == "ticket":
                prompt = boundary_prompt(fixture, repeat)
                if backend == "mock":
                    ticket = mock_ticket(fixture, repeat); meta["finish_reason"] = "mock"
                else:
                    raw = llm.chat_completion([{"role": "user", "content": prompt}], temperature=fixture["boundary_maker"]["temperature"], max_tokens=fixture["boundary_maker"]["max_tokens"], json_mode=True, max_retries=0, completion_metadata_sink=meta.update)
                    parsed = json.loads(raw); handling = parsed.get("store_item_temporary_handling", parsed.get("locked_boundaries", {}).get("store_item_temporary_handling"))
                    ticket = build_ticket(fixture, repeat, handling)
                validate_ticket(ticket, fixture)
                compile_start = time.perf_counter(); summary = compile_summary(ticket); validate_summary(summary, ticket)
                summary_path = output_dir / "summaries" / f"SC4__W2__r{repeat}.json"
                write_json(summary_path, {**summary.model_dump(), "compiler_latency_ms": int((time.perf_counter() - compile_start) * 1000)})
                text = json.dumps(ticket.model_dump(), ensure_ascii=False)
                payload = {**ticket.model_dump(), "status": "mock_completed" if backend == "mock" else "completed", "backend": backend, "route_evidence": backend == "llm", "summary_path": str(summary_path), "usage": _usage(prompt, text, start, meta)}
            else:
                if arm == "W0":
                    prompt = w0_prompt(fixture); ticket_hash = summary_hash = None
                else:
                    ticket = _ticket_from_file(output_dir / "tickets" / f"SC4__W2__r{repeat}.json", fixture)
                    summary = _summary_from_file(output_dir / "summaries" / f"SC4__W2__r{repeat}.json", ticket)
                    prompt = w2_prompt(fixture, summary); ticket_hash, summary_hash = ticket.ticket_hash, summary.summary_hash
                if backend == "mock":
                    text = f"【MOCK PLACEHOLDER repeat {repeat} variant {'A' if arm == 'W0' else 'B'}；非小说正文】\n\n【仅验证管线，不得作为路线证据】"; meta["finish_reason"] = "mock"
                else:
                    text = llm.chat_completion([{"role": "user", "content": prompt}], temperature=fixture["prose"]["temperature"], max_tokens=fixture["prose"]["max_tokens"], max_retries=0, completion_metadata_sink=meta.update)
                payload = {"schema_version": "1.1", "scene_id": "SC4", "arm": arm, "repeat": repeat, "status": "mock_completed" if backend == "mock" else "completed", "backend": backend, "route_evidence": backend == "llm", "placeholder_not_fiction": backend == "mock", "text": text, "consumed_ticket_hash": ticket_hash, "consumed_summary_hash": summary_hash, "structured_ticket_routed_to_realizer": False, "narrative_organization_fields_routed": False, "usage": _usage(prompt, text, start, meta), "copy_safety": _copy(text, meta, backend)}
            write_json(path, payload); call["status"] = payload["status"]; completed += 1
        except Exception as exc:
            write_json(path, {"schema_version": "1.1", "call_id": call["call_id"], "status": "failed", "error_type": type(exc).__name__, "error": str(exc)})
            call["status"] = "failed"; write_json(plan_path, plan); raise
    write_json(plan_path, plan)
    return {"backend": backend, "completed_calls": completed, "planned_calls": 6, "route_evidence": backend == "llm"}


def anonymise_output(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    plan = read_json(output_dir / "boundary-canary-plan.json"); rows = []
    for call in plan["calls"]:
        if call["stage"] == "prose":
            row = read_json(Path(call["result_path"]))
            if row["status"] not in {"completed", "mock_completed"}: raise ValueError("four prose stages required")
            row["result_path"] = call["result_path"]; rows.append(row)
    public, private, review_template = anonymise(rows, 20260810)
    write_json(output_dir / "boundary-canary-blind-public.json", public)
    write_json(output_dir / "boundary-canary-blind-key.private.json", private)
    write_json(output_dir / "boundary-canary-review-template.json", review_template)
    (output_dir / "boundary-canary-review-instructions.md").write_text(instructions(), encoding="utf-8")
    return {"text_count": 4, "pair_count": 2, "public_identity_leak": False}


def aggregate_output(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    public, private = read_json(output_dir / "boundary-canary-blind-public.json"), read_json(output_dir / "boundary-canary-blind-key.private.json")
    result = aggregate_reviews(public, private, [read_json(p) for p in sorted((output_dir / "reviews").glob("*.json"))])
    fixture = read_json(DEFAULT_FIXTURE); tickets, summaries = {}, {}; valid = True
    for r in (1, 2):
        try:
            tickets[r] = _ticket_from_file(output_dir / "tickets" / f"SC4__W2__r{r}.json", fixture)
            summaries[r] = _summary_from_file(output_dir / "summaries" / f"SC4__W2__r{r}.json", tickets[r])
        except Exception: valid = False
    prose = [read_json(output_dir / "results" / f"SC4__{a}__r{r}.json") for r in (1, 2) for a in ("W0", "W2")]
    gates = {
        "two_boundary_tickets_valid_and_locked": valid and len(tickets) == 2,
        "source_contract_hashes_valid": valid,
        "two_summaries_deterministic": valid and len(summaries) == 2,
        "summary_hashes_valid": valid,
        "structured_ticket_not_routed_to_realizer": all(not x["structured_ticket_routed_to_realizer"] for x in prose if x["arm"] == "W2"),
        "narrative_organization_fields_absent": all(not x["narrative_organization_fields_routed"] for x in prose if x["arm"] == "W2"),
        "w2_consumed_ticket_hash_matches": all(x["consumed_ticket_hash"] == tickets[x["repeat"]].ticket_hash for x in prose if x["arm"] == "W2"),
        "w2_consumed_summary_hash_matches": all(x["consumed_summary_hash"] == summaries[x["repeat"]].summary_hash for x in prose if x["arm"] == "W2"),
        "truncation_zero": not any(x["copy_safety"]["truncation"] for x in prose),
        "exact_sentence_copy_zero": not any(x["copy_safety"]["exact_sentence_copy"] for x in prose),
        "shared_12gram_zero": not any(x["copy_safety"]["shared_12gram"] for x in prose),
    }
    result["machine_gates"] = gates
    if not all(gates.values()): result["conclusion"] = "do_not_expand_yet"
    write_json(output_dir / "boundary-canary-review-aggregate.json", result); return result


def main() -> None:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="action", required=True)
    for name in ("plan", "mock", "estimate", "run", "anonymise", "aggregate"):
        p = sub.add_parser(name); p.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE); p.add_argument("--output-dir", type=Path)
        if name in ("run", "mock"): p.add_argument("--rerun-id")
        if name == "run": p.add_argument("--enable-real-calls", action="store_true")
    args = parser.parse_args(); out = args.output_dir or (DEFAULT_REAL_OUTPUT if args.action == "run" else DEFAULT_OUTPUT)
    if args.action == "plan": result = build_plan(args.fixture, out)
    elif args.action == "estimate": result = estimate_budget(args.fixture)
    elif args.action == "mock": result = run(args.fixture, out, backend="mock", rerun_id=args.rerun_id)
    elif args.action == "run": result = run(args.fixture, out, backend="llm", allow_real_calls=args.enable_real_calls, rerun_id=args.rerun_id)
    elif args.action == "anonymise": result = anonymise_output(out)
    else: result = aggregate_output(out)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
