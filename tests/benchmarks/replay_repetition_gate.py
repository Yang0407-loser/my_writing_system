"""Replay the anti-repetition gate over a completed task's assembled output.

Purpose: acceptance evidence for the deterministic anti-copy layer. Feed it a
real `output/*.md` file; it splits the document into 【标题】 blocks and runs
`check_subsection_quality` progressively — each block judged against all
blocks before it, exactly as Writer would have seen them.

The LLM beat judge is stubbed to ALWAYS PASS. That is the point: whatever this
replay rejects is guaranteed by the deterministic layers alone, under the
worst-case assumption that the LLM judge approves everything (which is how the
2026-07-26 S1.4 byte-copy originally slipped through).

This script contains no prose and writes nothing; it only reads the file you
point it at.

Usage:
    uv run python tests/benchmarks/replay_repetition_gate.py output/<file>.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from app import repetition_checker as rc


TITLE_RE = re.compile(r"^【(.+)】$")


def split_blocks(text: str) -> list[tuple[str, str]]:
    """Split an assembled output document into (title, body) blocks."""
    blocks: list[tuple[str, str]] = []
    title: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        match = TITLE_RE.match(line.strip())
        if match:
            if title is not None:
                blocks.append((title, "\n".join(buf).strip()))
            title, buf = match.group(1), []
        elif title is not None:
            buf.append(line)
    if title is not None:
        blocks.append((title, "\n".join(buf).strip()))
    return [(t, b) for t, b in blocks if b]


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    path = Path(sys.argv[1])
    blocks = split_blocks(path.read_text(encoding="utf-8"))
    if not blocks:
        raise SystemExit(f"未在 {path} 中找到任何【标题】块")

    judge_calls: list[int] = []

    def always_pass_judge(prev_text: str, curr_text: str) -> dict:
        judge_calls.append(1)
        return {"advanced": True, "what": "replay-stub"}

    rc.llm_check_beat_advancement = always_pass_judge

    print(f"file: {path}")
    print(f"blocks: {len(blocks)}\n")
    print(f"{'#':>2}  {'字数':>6}  {'pass':5}  {'sim':>6}  {'确定性拒绝':<22}  LLM被询问  标题")

    previous: list[str] = []
    rejected = 0
    for index, (title, body) in enumerate(blocks, 1):
        judge_calls.clear()
        quality = rc.check_subsection_quality(
            body, previous, previous[-1] if previous else "", ""
        )
        if not quality["pass"]:
            rejected += 1
        print(
            f"{index:>2}  {len(body):>6}  {str(quality['pass']):5}  "
            f"{quality['repetition']['max_similarity']:>6}  "
            f"{str(quality.get('deterministic_reject')):<22}  "
            f"{'是' if judge_calls else '否':^8}  【{title[:24]}】"
        )
        previous.append(body)

    print(f"\nrejected={rejected}/{len(blocks)} (LLM 判定被 stub 为永远放行)")
    if rejected == 0:
        print("提示: 若该文件包含已知复制小节而此处 0 拦截，则确定性层失效。")


if __name__ == "__main__":
    main()
