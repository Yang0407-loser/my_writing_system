from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.utils.llm_client import estimate_messages_tokens, estimate_tokens

from .prompts import SYSTEM_PROMPT, build_generation_messages


ROOT = Path(__file__).resolve().parents[2]


def compute_legacy_d_component_breakdown(
    manifest_path: Path,
    prepared_path: Path,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
    output: dict[str, Any] = {}
    for style_id, prepared_style in prepared["styles"].items():
        contract = prepared_style["style_contract"]
        stable_components = {
            "1_writer_system": SYSTEM_PROMPT,
            "3_positive_principles": "\n".join(contract["positive_principles"]),
            "4_prohibitions": "\n".join(contract["prohibitions"]),
            "5_distance_viewpoint": contract["narrative_distance_and_viewpoint"],
            "6_sentence_paragraph_rhythm": contract["sentence_and_paragraph_rhythm"],
            "7_diction_imagery_sensory": contract["diction_imagery_and_sensory_sources"],
            "8_emotional_expression": contract["emotional_expression"],
            "9_scene_adaptation": "\n".join(
                f"{key}：{value}" for key, value in contract["scene_adaptation"].items()
            ),
            "10_positive_examples": "\n".join(contract["positive_examples"]),
            "11_negative_examples": "\n".join(
                item["text"] for item in contract["negative_examples"]
            ),
            "12_negative_reasons": "\n".join(
                item["reason"] for item in contract["negative_examples"]
            ),
        }
        rows = []
        for scene in manifest["scenes"]:
            scene_task = "\n".join(
                [
                    scene["prompt"],
                    *manifest["shared_context"]["characters"],
                    *manifest["shared_context"]["world_facts"],
                    *scene["mandatory_events"],
                    *scene["forbidden_events"],
                    f"target_chars={manifest['experiment']['target_chars']}",
                ]
            )
            components = {
                **stable_components,
                "2_scene_characters_world_facts": scene_task,
                "13_scene_modulation": scene["style_modulation"],
            }
            messages = build_generation_messages(
                arm="D",
                prepared=prepared_style,
                scene=scene,
                shared_context=manifest["shared_context"],
                target_chars=manifest["experiment"]["target_chars"],
            )
            estimated_total = estimate_messages_tokens(messages)
            tokens = {key: estimate_tokens(value) for key, value in components.items()}
            rows.append(
                {
                    "scene_id": scene["id"],
                    "component_characters": {
                        key: len(value) for key, value in components.items()
                    },
                    "component_estimated_tokens": tokens,
                    "rendering_labels_and_scaffold_estimated_tokens": (
                        estimated_total - sum(tokens.values())
                    ),
                    "estimated_prompt_total": estimated_total,
                }
            )
        output[style_id] = rows
    return {
        "method": "app.utils.llm_client.estimate_tokens; CJK≈1.5 tokens, other≈0.75, ±15%",
        "styles": output,
    }


def main() -> None:
    payload = compute_legacy_d_component_breakdown(
        ROOT / "experiments" / "style_control" / "fixtures" / "experiment_manifest.json",
        ROOT / "outputs" / "style-control-experiment-real" / "prepared-style-inputs.json",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
