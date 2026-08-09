"""CLI for the non-production Reality Repair Probe v0."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from app.config import settings
from app.utils.llm_client import get_llm_client
from app.utils.prompt_templates import TARGETED_REVISE_PROMPT

from .runner import run_repair_probe


_SECTION_START_RE = re.compile(r"(?m)^[^\r\n]+\r?\n\d+/\d+\r?\n")


def split_generated_sections(text: str) -> list[str]:
    starts = [match.start() for match in _SECTION_START_RE.finditer(text)]
    if not starts:
        return [text.strip()]
    sections: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        prefix = text[:start].strip() if index == 0 else ""
        chunk = text[start:end].strip()
        sections.append(f"{prefix}\n{chunk}".strip())
    return sections


def _render_markdown(result: dict) -> str:
    lines = [
        "# Reality Repair Probe v0",
        "",
        f"- 原始 warning：{result['original_warning_count']}",
        f"- 已消除 warning：{result['resolved_warning_count']}",
        f"- 剩余 warning：{result['remaining_warning_count']}",
        f"- 新增 warning：{result['new_warning_count']}",
        f"- warning 消除率：{result['resolution_rate']:.1%}",
        f"- 全文改动比例：{result['combined_changed_ratio']:.1%}",
        f"- 自动门槛：{'通过' if result['automatic_criteria_pass'] else '未通过'}",
        f"- 晋级状态：{result['promotion_status']}",
        "",
    ]
    for case in result["cases"]:
        lines.extend(
            [
                f"## 小节 {case['subsection']}",
                "",
                f"- 修订前：{', '.join(case['before_warning_codes']) or '无'}",
                f"- 修订后：{', '.join(case['after_warning_codes']) or '无'}",
                f"- 改动比例：{case['changed_ratio']:.1%}",
                "",
                "### A：原稿",
                "",
                case["original"],
                "",
                "### B：修订稿",
                "",
                case["revised"],
                "",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--known-context", default="")
    parser.add_argument("--allowed-names", default="")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    sections = split_generated_sections(input_path.read_text(encoding="utf-8"))
    client = get_llm_client(model=settings.WRITER_LLM_MODEL)

    def revise(original: str, instruction: str) -> str:
        prompt = TARGETED_REVISE_PROMPT.format(
            original_text=original,
            instruction=instruction,
        )
        return client.chat_completion(
            [
                {
                    "role": "system",
                    "content": (
                        "你是叙事事实修订编辑。只修复明确列出的客观冲突，"
                        "不得进行风格润色，只输出完整修订文本。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=4096,
            prompt_name="reality_repair_probe_v0",
        )

    result = run_repair_probe(
        sections,
        revise=revise,
        known_context=args.known_context,
        allowed_names=[
            name.strip() for name in args.allowed_names.split(",") if name.strip()
        ],
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output_path.with_suffix(".md").write_text(
        _render_markdown(result), encoding="utf-8"
    )
    print(json.dumps({
        key: result[key]
        for key in (
            "revision_calls",
            "original_warning_count",
            "resolved_warning_count",
            "remaining_warning_count",
            "new_warning_count",
            "resolution_rate",
            "combined_changed_ratio",
            "automatic_criteria_pass",
            "promotion_status",
        )
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
