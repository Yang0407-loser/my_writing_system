"""CLI for the non-production Scene Reality Contract v0 experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import settings
from app.utils.llm_client import get_llm_client

from . import runner
from .contract import SCENE_REALITY_CONTRACT_V0_TEXT
from .inputs import load_experiment_inputs
from .prompting import build_prompt_artifact, build_prompt_values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, help="A 稿原文路径")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--task-id", default="scene-reality-contract-v0")
    parser.add_argument("--dry-run", action="store_true", help="只构建 Prompt 快照，不调用模型")
    args = parser.parse_args()

    baseline_text = Path(args.baseline).read_text(encoding="utf-8")
    inputs = load_experiment_inputs()
    reports_dir = Path(args.reports_dir)
    client = get_llm_client(model=settings.WRITER_LLM_MODEL)

    if args.dry_run:
        prompts = _build_all_prompts(inputs, args.task_id)
        snapshot = runner._render_prompt_snapshot(prompts)
        prompt_path = reports_dir / "scene-reality-contract-v0-prompt-2026-08-02.txt"
        reports_dir.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(snapshot, encoding="utf-8")
        print(f"dry-run Prompt 快照已保存: {prompt_path.resolve()}")
        return 0

    def generate_call(messages: list[dict], max_tokens: int) -> str:
        return client.chat_completion(
            messages,
            temperature=0.5,
            max_tokens=max_tokens,
            top_p=0.9,
            max_retries=0,
            prompt_name="scene_reality_contract_v0",
        )

    result = runner.run_experiment(
        baseline_text=baseline_text,
        generate=generate_call,
        inputs=inputs,
        contract_text=SCENE_REALITY_CONTRACT_V0_TEXT,
        task_id=args.task_id,
        reports_dir=reports_dir,
    )
    b_draft = runner._render_b_draft(result["candidate_sections"])
    paths = runner.write_outputs(result, reports_dir, b_draft)
    print(json.dumps({
        "model": result["model"],
        "generation_calls": result["generation_calls"],
        "revision_calls": result["revision_calls"],
        "baseline_warning_count": result["baseline_warning_count"],
        "candidate_warning_count": result["candidate_warning_count"],
        "resolved_issue_count": result["resolved_issue_count"],
        "new_warning_codes": result["new_warning_codes"],
        "automatic_criteria_pass": result["automatic_criteria_pass"],
        "promotion_status": result["promotion_status"],
        "outputs": paths,
    }, ensure_ascii=False))
    return 0


def _build_all_prompts(inputs, task_id: str) -> list[dict]:
    prompts = []
    prev_b_texts: list[str] = []
    sec = inputs.sections[0]
    for sub in sec.get("subsections", []):
        sub_num = int(sub.get("subsection", 0))
        target_words = int(sub.get("target_words", 2000))
        values = build_prompt_values(
            inputs,
            section=1,
            sub_num=sub_num,
            prev_b_texts=prev_b_texts,
            contract_text=SCENE_REALITY_CONTRACT_V0_TEXT,
        )
        artifact = build_prompt_artifact(
            inputs,
            values,
            section=1,
            sub_num=sub_num,
            task_id=task_id,
            target_words=target_words,
        )
        prompts.append(
            {
                "subsection": sub_num,
                "title": sub.get("title", ""),
                "target_words": target_words,
                "prompt_hash": artifact.messages_hash,
                "messages": artifact.messages,
            }
        )
        prev_b_texts.append("")
    return prompts


if __name__ == "__main__":
    raise SystemExit(main())
