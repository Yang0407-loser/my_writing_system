from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .aggregate import aggregate_reviews
from .corpus import build_shadow_corpus
from .models import SceneDecisionTicket, ShadowCorpus
from .review import build_review_template, review_instructions
from .ticket import compile_ticket


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = (
    ROOT
    / "experiments"
    / "style_control"
    / "fixtures"
    / "style_contract_ablation_action_bridge_manifest.json"
)
DEFAULT_PUBLIC_CORPUS = (
    ROOT
    / "outputs"
    / "style-contract-ablation-action-bridge-real"
    / "blind-review-public.json"
)
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "writer-decision-ticket-shadow-v0"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_raw_json(path: Path) -> tuple[str, dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    return raw, json.loads(raw)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_shadow_package(
    manifest_path: Path = DEFAULT_MANIFEST,
    public_corpus_path: Path = DEFAULT_PUBLIC_CORPUS,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    # Isolation invariant: compile and freeze the ticket before reading prose.
    manifest_raw, manifest = _read_raw_json(manifest_path)
    ticket = compile_ticket(manifest)
    ticket_snapshot = ticket.model_dump()

    public_raw, public_payload = _read_raw_json(public_corpus_path)
    corpus = build_shadow_corpus(
        public_payload,
        source_public_raw=public_raw,
    )
    generic_template = build_review_template(ticket, corpus)

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "reviews").mkdir(exist_ok=True)
    template_dir = output_dir / "review-templates"
    template_dir.mkdir(exist_ok=True)
    _write_json(output_dir / "decision-ticket-public.json", ticket_snapshot)
    _write_json(output_dir / "shadow-corpus-public.json", corpus.model_dump())
    _write_json(
        output_dir / "decision-witness-review-template.json",
        generic_template,
    )
    for index in range(1, 4):
        reviewer_id = f"validator-{index:02d}"
        _write_json(
            template_dir / f"{reviewer_id}.template.json",
            build_review_template(ticket, corpus, reviewer_id=reviewer_id),
        )
    (output_dir / "decision-witness-review-instructions.md").write_text(
        review_instructions(),
        encoding="utf-8",
    )
    provenance = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "build_order": [
            "read_manifest",
            "compile_and_freeze_ticket",
            "read_public_anonymous_corpus",
            "paragraphise_without_text_changes",
            "build_blank_review_materials",
        ],
        "ticket_compiled_before_corpus_read": True,
        "allowed_inputs": {
            "manifest_path": str(manifest_path.resolve()),
            "manifest_sha256": _sha(manifest_raw),
            "public_corpus_path": str(public_corpus_path.resolve()),
            "public_corpus_sha256": _sha(public_raw),
        },
        "prohibited_inputs_not_used_by_builder": [
            "blind-review-key.private.json",
            "prior reviews",
            "prior aggregate",
            "prompts",
            "results",
        ],
        "ticket_hash": ticket.ticket_hash,
        "compact_rendering_hash": ticket.compact_rendering_hash,
        "corpus_sample_hashes": {
            sample.blind_id: sample.text_sha256 for sample in corpus.samples
        },
        "new_prose_generated": False,
        "llm_calls_made": 0,
        "production_code_changed": False,
    }
    _write_json(
        output_dir / "decision-ticket-provenance.private.json",
        provenance,
    )
    build_manifest = {
        "schema_version": "1.0",
        "ticket_id": ticket.ticket_id,
        "ticket_hash": ticket.ticket_hash,
        "compact_ticket_tokens": ticket.ticket_token_estimate,
        "source_coverage": 1.0,
        "sample_count": corpus.sample_count,
        "paragraph_count": sum(len(item.paragraphs) for item in corpus.samples),
        "reviewer_templates": 3,
        "reviews_present": 0,
        "aggregate_ready": False,
        "route_effect_conclusion_allowed": False,
    }
    _write_json(output_dir / "shadow-build-manifest.json", build_manifest)
    return build_manifest


def aggregate_shadow(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    ticket = SceneDecisionTicket.model_validate(
        json.loads(
            (output_dir / "decision-ticket-public.json").read_text(encoding="utf-8")
        )
    )
    corpus = ShadowCorpus.model_validate(
        json.loads(
            (output_dir / "shadow-corpus-public.json").read_text(encoding="utf-8")
        )
    )
    review_paths = sorted((output_dir / "reviews").glob("validator-*.json"))
    raw_reviews = [
        json.loads(path.read_text(encoding="utf-8")) for path in review_paths
    ]
    payload = aggregate_reviews(ticket, corpus, raw_reviews)
    _write_json(output_dir / "decision-witness-aggregate.json", payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    build = sub.add_parser("build")
    build.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    build.add_argument("--public-corpus", type=Path, default=DEFAULT_PUBLIC_CORPUS)
    build.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    aggregate = sub.add_parser("aggregate")
    aggregate.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    if args.action == "build":
        result = build_shadow_package(
            args.manifest,
            args.public_corpus,
            args.output_dir,
        )
    else:
        result = aggregate_shadow(args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

