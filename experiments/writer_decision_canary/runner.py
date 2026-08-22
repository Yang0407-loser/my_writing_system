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
from .prompts import decision_prompt, w0_prompt, w1_prompt
from .review import reviewer_instructions
from .ticket import build_ticket, mock_ticket, validate_ticket


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = ROOT / "experiments/writer_decision_canary/fixtures/writer_decision_canary_sc3.json"
DEFAULT_OUTPUT = ROOT / "outputs/writer-decision-canary-v1"
DEFAULT_REAL_OUTPUT = ROOT / "outputs/writer-decision-canary-v1-real"
COPY_AUDIT_REFERENCE = (
    ROOT / "experiments/style_control/fixtures/reference_dialogue_noir.txt"
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_plan(fixture_path: Path = DEFAULT_FIXTURE, output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    fixture = read_json(fixture_path)
    calls = []
    for repeat in (1, 2):
        seed = fixture["experiment"]["base_seed"] + repeat
        calls.extend([
            {
                "call_id": f"SC3__W0__r{repeat}:prose", "arm": "W0", "repeat": repeat,
                "stage": "prose", "seed": seed, "status": "pending",
                "result_path": str(output_dir / "results" / f"SC3__W0__r{repeat}.json"),
            },
            {
                "call_id": f"SC3__W1__r{repeat}:ticket", "arm": "W1", "repeat": repeat,
                "stage": "ticket", "seed": seed, "status": "pending",
                "result_path": str(output_dir / "tickets" / f"SC3__W1__r{repeat}.json"),
            },
            {
                "call_id": f"SC3__W1__r{repeat}:prose", "arm": "W1", "repeat": repeat,
                "stage": "prose", "seed": seed, "status": "pending",
                "result_path": str(output_dir / "results" / f"SC3__W1__r{repeat}.json"),
            },
        ])
    plan = {
        "schema_version": "1.0", "scene_id": "SC3",
        "fixture_path": str(fixture_path), "real_calls_enabled_by_fixture": False,
        "planned_call_count": 6, "planned_text_count": 4, "pair_count": 2,
        "calls": calls,
    }
    write_json(output_dir / "mini-canary-plan.json", plan)
    return plan


def estimate_budget(fixture_path: Path = DEFAULT_FIXTURE) -> dict[str, Any]:
    fixture = read_json(fixture_path)
    ticket_inputs = sum(estimate_tokens(decision_prompt(fixture, r)) for r in (1, 2))
    w0_inputs = 2 * estimate_tokens(w0_prompt(fixture))
    # W1 prompt depends on ticket; valid deterministic tickets make the estimate reproducible.
    w1_inputs = sum(estimate_tokens(w1_prompt(fixture, mock_ticket(fixture, r))) for r in (1, 2))
    return {
        "real_call_count": 6,
        "decision_calls": 2,
        "prose_calls": 4,
        "estimated_input_tokens": ticket_inputs + w0_inputs + w1_inputs,
        "max_output_token_budget": 2 * fixture["decision_maker"]["max_tokens"] + 4 * fixture["prose"]["max_tokens"],
        "target_prose_chars_total": 4 * fixture["prose"]["target_chars"],
        "note": "Input is heuristic; output maximum is a cap, not expected consumption.",
    }


def _usage(prompt: str, text: str, started: float, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "input_tokens": int(metadata.get("input_tokens", estimate_tokens(prompt))),
        "output_tokens": int(metadata.get("output_tokens", estimate_tokens(text))),
        "latency_ms": int(float(metadata.get("latency_seconds", time.perf_counter() - started)) * 1000),
        "finish_reason": str(metadata.get("finish_reason", "unknown")),
    }


def _mock_placeholder(arm: str, repeat: int) -> str:
    variant = "A" if arm == "W0" else "B"
    return (
        f"【MOCK PLACEHOLDER repeat {repeat} variant {variant}；非小说正文】\n\n"
        "【仅验证恢复、匿名化与评审管线；不得作为路线证据】"
    )


def run(
    fixture_path: Path = DEFAULT_FIXTURE,
    output_dir: Path = DEFAULT_OUTPUT,
    *,
    backend: str,
    allow_real_calls: bool = False,
    rerun_id: str | None = None,
) -> dict[str, Any]:
    if backend == "llm" and not allow_real_calls:
        raise PermissionError("real calls are disabled; pass --enable-real-calls explicitly")
    fixture = read_json(fixture_path)
    plan_path = output_dir / "mini-canary-plan.json"
    plan = read_json(plan_path) if plan_path.exists() else build_plan(fixture_path, output_dir)
    llm = get_llm_client() if backend == "llm" else None
    completed = 0
    for call in plan["calls"]:
        if rerun_id and call["call_id"] != rerun_id:
            continue
        path = Path(call["result_path"])
        if path.exists():
            existing = read_json(path)
            if rerun_id and existing.get("status") != "failed":
                raise ValueError("rerun-id is restricted to a failed call")
            if not rerun_id and existing.get("status") in {"completed", "mock_completed"}:
                if stage := call.get("stage"):
                    if stage == "ticket":
                        from .models import DecisionTicket
                        validate_ticket(
                            DecisionTicket.model_validate({
                                key: existing[key] for key in DecisionTicket.model_fields
                            }),
                            fixture,
                        )
                call["status"] = existing["status"]
                completed += 1
                continue
        repeat, stage, arm = call["repeat"], call["stage"], call["arm"]
        started = time.perf_counter()
        metadata: dict[str, Any] = {}
        try:
            if stage == "ticket":
                prompt = decision_prompt(fixture, repeat)
                if backend == "mock":
                    ticket = mock_ticket(fixture, repeat)
                    metadata["finish_reason"] = "mock"
                else:
                    raw = llm.chat_completion(
                        [{"role": "user", "content": prompt}],
                        temperature=fixture["decision_maker"]["temperature"],
                        max_tokens=fixture["decision_maker"]["max_tokens"],
                        json_mode=True, max_retries=0,
                        completion_metadata_sink=metadata.update,
                    )
                    parsed = json.loads(raw)
                    selected = parsed.get("selected_decisions", parsed)
                    ticket = build_ticket(fixture, repeat, selected)
                validate_ticket(ticket, fixture)
                text = json.dumps(ticket.model_dump(), ensure_ascii=False)
                payload = {
                    **ticket.model_dump(), "status": "mock_completed" if backend == "mock" else "completed",
                    "backend": backend, "route_evidence": backend == "llm",
                    "usage": _usage(prompt, text, started, metadata),
                }
            else:
                if arm == "W0":
                    prompt = w0_prompt(fixture)
                    consumed = None
                else:
                    ticket_path = output_dir / "tickets" / f"SC3__W1__r{repeat}.json"
                    if not ticket_path.exists():
                        raise RuntimeError("validated ticket must complete before W1 prose")
                    from .models import DecisionTicket
                    raw_ticket = read_json(ticket_path)
                    ticket = validate_ticket(
                        DecisionTicket.model_validate({
                            key: raw_ticket[key]
                            for key in DecisionTicket.model_fields
                        }),
                        fixture,
                    )
                    prompt = w1_prompt(fixture, ticket)
                    consumed = ticket.ticket_hash
                if backend == "mock":
                    text = _mock_placeholder(arm, repeat)
                    metadata["finish_reason"] = "mock"
                else:
                    text = llm.chat_completion(
                        [{"role": "user", "content": prompt}],
                        temperature=fixture["prose"]["temperature"],
                        max_tokens=fixture["prose"]["max_tokens"],
                        max_retries=0, completion_metadata_sink=metadata.update,
                    )
                payload = {
                    "schema_version": "1.0", "scene_id": "SC3", "arm": arm,
                    "repeat": repeat, "status": "mock_completed" if backend == "mock" else "completed",
                    "backend": backend, "route_evidence": backend == "llm",
                    "text": text, "consumed_ticket_hash": consumed,
                    "usage": _usage(prompt, text, started, metadata),
                    "copy_safety": (
                        {
                            "evaluated": False,
                            "truncation": False,
                            "exact_sentence_copy": False,
                            "shared_12gram": False,
                            "note": "mock placeholder is not route evidence",
                        }
                        if backend == "mock"
                        else _copy_safety(text, metadata)
                    ),
                }
            write_json(path, payload)
            call["status"] = payload["status"]
            completed += 1
        except Exception as exc:
            write_json(path, {
                "schema_version": "1.0", "call_id": call["call_id"],
                "status": "failed", "error_type": type(exc).__name__, "error": str(exc),
            })
            call["status"] = "failed"
            write_json(plan_path, plan)
            raise
    write_json(plan_path, plan)
    return {
        "backend": backend, "completed_calls": completed, "planned_calls": 6,
        "route_evidence": backend == "llm",
    }


def _copy_safety(text: str, metadata: dict[str, Any]) -> dict[str, Any]:
    # Audit-only: the reference is never included in either generation prompt.
    reference = COPY_AUDIT_REFERENCE.read_text(encoding="utf-8")
    metrics = inspect_overlap(text, reference)
    return {
        "evaluated": True,
        "audit_reference_path": str(COPY_AUDIT_REFERENCE),
        "reference_routed_to_prompt": False,
        "truncation": metadata.get("finish_reason") == "length",
        "exact_sentence_copy": metrics["exact_copied_sentence_count"] > 0,
        "shared_12gram": metrics["shared_12gram_unique_count"] > 0,
        "metrics": metrics,
    }


def anonymise_output(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    plan = read_json(output_dir / "mini-canary-plan.json")
    results = []
    for call in plan["calls"]:
        if call["stage"] != "prose":
            continue
        row = read_json(Path(call["result_path"]))
        if row.get("status") not in {"completed", "mock_completed"}:
            raise ValueError("all four prose stages must be complete")
        row["result_path"] = call["result_path"]
        results.append(row)
    public, private, template = anonymise(results, 20260731)
    write_json(output_dir / "mini-canary-blind-public.json", public)
    write_json(output_dir / "mini-canary-blind-key.private.json", private)
    write_json(output_dir / "mini-canary-review-template.json", template)
    (output_dir / "mini-canary-review-instructions.md").write_text(
        reviewer_instructions(), encoding="utf-8"
    )
    return {"text_count": 4, "pair_count": 2, "public_identity_leak": False}


def aggregate_output(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    public = read_json(output_dir / "mini-canary-blind-public.json")
    private = read_json(output_dir / "mini-canary-blind-key.private.json")
    paths = sorted((output_dir / "reviews").glob("*.json"))
    result = aggregate_reviews(public, private, [read_json(path) for path in paths])
    fixture = read_json(DEFAULT_FIXTURE)
    ticket_hashes = {}
    tickets_valid = True
    for repeat in (1, 2):
        try:
            from .models import DecisionTicket
            raw = read_json(output_dir / "tickets" / f"SC3__W1__r{repeat}.json")
            ticket = validate_ticket(
                DecisionTicket.model_validate({
                    key: raw[key] for key in DecisionTicket.model_fields
                }),
                fixture,
            )
            ticket_hashes[repeat] = ticket.ticket_hash
        except Exception:
            tickets_valid = False
    prose_rows = [
        read_json(output_dir / "results" / f"SC3__{arm}__r{repeat}.json")
        for repeat in (1, 2) for arm in ("W0", "W1")
    ]
    consumed_match = all(
        row["consumed_ticket_hash"] == ticket_hashes.get(row["repeat"])
        for row in prose_rows if row["arm"] == "W1"
    )
    copy_gates = {
        "truncation_zero": not any(row["copy_safety"]["truncation"] for row in prose_rows),
        "exact_sentence_copy_zero": not any(row["copy_safety"]["exact_sentence_copy"] for row in prose_rows),
        "shared_12gram_zero": not any(row["copy_safety"]["shared_12gram"] for row in prose_rows),
    }
    result["machine_gates"] = {
        "two_tickets_valid_and_locked": tickets_valid and len(ticket_hashes) == 2,
        "w1_consumed_ticket_hash_matches": consumed_match,
        **copy_gates,
    }
    if not all(result["machine_gates"].values()):
        result["conclusion"] = "do_not_expand_yet"
    write_json(output_dir / "mini-canary-review-aggregate.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("plan", "mock", "estimate", "run", "anonymise", "aggregate"):
        cmd = sub.add_parser(action)
        cmd.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
        cmd.add_argument("--output-dir", type=Path, default=None)
        if action == "run":
            cmd.add_argument("--enable-real-calls", action="store_true")
            cmd.add_argument("--rerun-id")
        if action == "mock":
            cmd.add_argument("--rerun-id")
    args = parser.parse_args()
    output = args.output_dir or (DEFAULT_REAL_OUTPUT if args.action == "run" else DEFAULT_OUTPUT)
    if args.action == "plan":
        result = build_plan(args.fixture, output)
    elif args.action == "estimate":
        result = estimate_budget(args.fixture)
    elif args.action == "mock":
        result = run(args.fixture, output, backend="mock", rerun_id=args.rerun_id)
    elif args.action == "run":
        result = run(args.fixture, output, backend="llm", allow_real_calls=args.enable_real_calls, rerun_id=args.rerun_id)
    elif args.action == "anonymise":
        result = anonymise_output(output)
    else:
        result = aggregate_output(output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
