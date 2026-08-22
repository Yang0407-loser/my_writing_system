"""CLI for the four-call, two-scene expression-kernel replication."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import settings
from app.utils.llm_client import get_llm_client

from .runner import render_prompt_snapshot, run_replication, write_outputs


DATE_TAG = "2026-08-02"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    prompt_path = reports_dir / f"anti-ai-expression-kernel-v0-replication-prompt-{DATE_TAG}.txt"
    prompt_path.write_text(render_prompt_snapshot(), encoding="utf-8")
    if args.dry_run:
        print(str(prompt_path.resolve()))
        return 0

    private_path = reports_dir / f"anti-ai-expression-kernel-v0-replication-{DATE_TAG}.json"
    blind_path = reports_dir / f"anti-ai-expression-kernel-v0-replication-blind-{DATE_TAG}.md"
    existing = [str(path.resolve()) for path in (private_path, blind_path) if path.exists()]
    if existing:
        raise SystemExit("Refusing to rerun; real result already exists: " + ", ".join(existing))

    client = get_llm_client(model=settings.WRITER_LLM_MODEL)

    def generate(messages: list[dict]) -> str:
        return client.chat_completion(
            messages,
            temperature=0.7,
            top_p=0.9,
            max_tokens=2400,
            max_retries=0,
            prompt_name="anti_ai_expression_kernel_v0_replication",
        )

    result = run_replication(generate)
    paths = write_outputs(result, reports_dir)
    print(
        json.dumps(
            {
                "model": settings.WRITER_LLM_MODEL,
                "generation_calls": result["generation_calls"],
                "revision_calls": result["revision_calls"],
                "automatic_directional_pass": result["automatic_directional_pass"],
                "promotion_status": result["promotion_status"],
                "outputs": paths,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

