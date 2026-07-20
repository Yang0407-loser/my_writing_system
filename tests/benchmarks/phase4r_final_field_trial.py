"""Prepare and evaluate the final Phase 4R real-writing A/B field trial.

Private task state, messages, arm mappings, prose, and user review stay in the
gitignored ``.phase4r_final_trial_runtime`` directory.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import shutil
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from statistics import mean
from typing import Any
from unittest.mock import patch

import redis

from app.config import settings
from app.context_ab_shadow import messages_hash, messages_tokens
from app.utils.llm_client import get_llm_client
from app.utils.prompt_templates import HANDOVER_BRIEF_PROMPT, STYLE_BEHAVIOR_PROMPT
from app.utils.text_chunker import chunk_text
from app.vector_store import VectorStore
from app.writing.contracts import GenerationArtifact
from app.writing.scene_spec_provider import OutlineSceneSpecProvider


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME = ROOT / ".phase4r_final_trial_runtime"
PUBLIC_MANIFEST = ROOT / "reports" / "phase4r-final-real-writing-trial-package.json"
EXPECTED_SUBSECTIONS = 4
ARMS = ("legacy_full", "legacy_full_scene_spec")
SCENE_HEADER = "\n\n## SceneSpec（结构化写作约束）\n"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decode(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def snapshot_task(task_id: str, runtime_dir: Path) -> dict[str, Any]:
    client = redis.Redis.from_url(settings.REDIS_BACKEND_URL, decode_responses=True)
    task = client.hgetall(task_id)
    checkpoint = client.hgetall(f"checkpoint:{task_id}")
    if not task or not checkpoint:
        raise RuntimeError("active task and checkpoint are required")
    decoded_task = {key: _decode(value) for key, value in task.items()}
    decoded_checkpoint = {key: _decode(value) for key, value in checkpoint.items()}
    decoded_checkpoint.pop("api_key", None)
    outline = decoded_checkpoint.get("outline_v2") or decoded_task.get("outline") or []
    if len(outline) != 1 or len(outline[0].get("subsections", [])) != EXPECTED_SUBSECTIONS:
        raise ValueError("field trial requires exactly four consecutive subsections in one section")
    completed = decoded_checkpoint.get("draft") or {}
    if completed:
        raise ValueError("field trial inputs must be frozen before any target subsection is generated")
    private = {
        "schema_version": "phase4r-final-field-trial-private-v1",
        "task_id": task_id,
        "task": decoded_task,
        "checkpoint": decoded_checkpoint,
        "outline": outline,
        "frozen_contexts": _freeze_contexts(task_id, int(outline[0].get("section", 1))),
        "source": "live_redis_checkpoint",
    }
    private_path = runtime_dir / "source.private.json"
    _write_json(private_path, private)
    subsection_manifest = []
    for index, subsection in enumerate(outline[0]["subsections"], 1):
        canonical = json.dumps(subsection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        subsection_manifest.append({
            "trial_index": index,
            "section": int(outline[0].get("section", 1)),
            "subsection": index,
            "source_id": str(subsection.get("source_id", f"outline:S1.{index}")),
            "outline_hash": _sha256_text(canonical),
        })
    public = {
        "schema_version": "phase4r-final-field-trial-snapshot-v1",
        "status": "source_frozen_not_prepared",
        "task_id_hash": _sha256_text(task_id),
        "source_snapshot_sha256": hashlib.sha256(private_path.read_bytes()).hexdigest(),
        "source": "live_redis_checkpoint",
        "section_count": 1,
        "subsection_count": EXPECTED_SUBSECTIONS,
        "completed_target_subsections_at_snapshot": 0,
        "arms": list(ARMS),
        "planned_generation_calls": EXPECTED_SUBSECTIONS * len(ARMS),
        "private_runtime_gitignored": True,
        "contains_story_text": False,
        "subsections": subsection_manifest,
    }
    _write_json(runtime_dir / "snapshot.public.json", public)
    return public


def _freeze_contexts(task_id: str, section: int) -> dict[str, str]:
    from app import rule_store
    from app.character_relation_store import build_relation_context
    from app.experience_timeline import build_experience_context
    from app.faction_store import build_faction_context
    from app.foreshadowing_store import build_foreshadowing_context
    from app.item_manager import build_item_context
    from app.map_manager import build_location_context
    from app.subplot_manager import build_subplot_context

    return {
        "rules": rule_store.build_rules_context() or "",
        "subplots": build_subplot_context(task_id) or "",
        "relations": build_relation_context(task_id) or "",
        "experience": build_experience_context(task_id, chapter=999) or "",
        "foreshadowing": build_foreshadowing_context(task_id, section) or "",
        "factions": build_faction_context(task_id, section) or "",
        "locations": build_location_context(task_id) or "",
        "items": build_item_context(task_id) or "",
    }


def _scene_query(private: dict[str, Any], subsection: dict[str, Any]) -> str:
    section = private["outline"][0]
    parts = [str(private["checkpoint"].get("config_topic", "")), str(section.get("title", ""))]
    if subsection.get("title") != section.get("title"):
        parts.append(str(subsection.get("title", "")))
    parts.extend(str(value) for value in subsection.get("key_points", []) if value not in parts)
    return " ".join(value for value in parts if value)


def _compile_real_scene_spec(private: dict[str, Any], subsection: dict[str, Any]):
    section = int(private["outline"][0].get("section", 1))
    sub_number = int(subsection.get("subsection", 0))
    subsections = private["outline"][0]["subsections"]
    next_subsection = next(
        (item for item in private["outline"][0]["subsections"] if int(item.get("subsection", 0)) == sub_number + 1),
        None,
    )
    built = OutlineSceneSpecProvider().build(
        task_id=private["task_id"],
        section=section,
        current_subsection=subsection,
        next_subsection=next_subsection,
        is_last_subsection=subsection is subsections[-1],
    )
    if built.spec.estimated_tokens > 400:
        raise ValueError(f"SceneSpec for subsection {sub_number} exceeds 400 estimated tokens")
    return built.spec, built.rendered


def prepare_trial(runtime_dir: Path) -> dict[str, Any]:
    private = _read_json(runtime_dir / "source.private.json")
    snapshot_public = _read_json(runtime_dir / "snapshot.public.json")
    source_hash = snapshot_public["source_snapshot_sha256"]
    isolated_task = f"phase4r-final-{source_hash[:24]}"
    store = VectorStore()
    if not isolated_task.startswith("phase4r-final-"):
        raise AssertionError("unsafe isolated task id")
    store.cleanup_task(isolated_task)
    try:
        previous = str(private["checkpoint"].get("_prev_draft") or "")
        for chunk in chunk_text(previous, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP):
            store.add_text(text=chunk, metadata={
                "task_id": isolated_task, "section": 0, "subsection": 0,
                "title": "前作", "topic": private["checkpoint"].get("config_topic", ""),
            })
        scenes, private_scenes = [], []
        for index, subsection in enumerate(private["outline"][0]["subsections"], 1):
            query = _scene_query(private, subsection)
            rag_items = store.search_with_meta(query, k=settings.RAG_TOP_K, task_id=isolated_task)
            spec, rendered = _compile_real_scene_spec(private, subsection)
            private_scenes.append({
                "trial_index": index, "subsection": subsection,
                "query": query, "rag_items": rag_items,
                "scene_spec": spec.model_dump(mode="json"), "scene_spec_text": rendered,
            })
            scenes.append({
                "trial_index": index, "section": spec.section, "subsection": spec.subsection,
                "outline_hash": snapshot_public["subsections"][index - 1]["outline_hash"],
                "query_hash": _sha256_text(query),
                "rag_source_ids": [item.get("id", "") for item in rag_items],
                "scene_spec_hash": spec.spec_hash,
                "scene_spec_tokens": spec.estimated_tokens,
                "scene_spec_source_ids": [item.source_id for item in spec.evidence],
            })
    finally:
        store.cleanup_task(isolated_task)
    private_package = {
        "schema_version": "phase4r-final-field-trial-package-private-v1",
        "source_snapshot_sha256": source_hash,
        "source": private,
        "scenes": private_scenes,
    }
    _write_json(runtime_dir / "package.private.json", private_package)
    result = {
        "schema_version": "phase4r-final-field-trial-package-v1",
        "status": "prepared_not_generated",
        "source_snapshot_sha256": source_hash,
        "model": settings.LLM_MODEL,
        "base_url_host": settings.LLM_BASE_URL.split("//", 1)[-1].split("/", 1)[0],
        "main_writer_calls": 8,
        "shared_auxiliary_calls": 2,
        "estimated_main_input_tokens": 8 * 12500,
        "max_main_output_tokens": sum(
            int(item.get("target_words", 2000)) * 4 for item in private["outline"][0]["subsections"]
        ) * 2,
        "arms": list(ARMS),
        "only_variable": "SceneSpec appended to the user message",
        "edit_cost_role": "optional_diagnostic",
        "scene_count": 4,
        "private_runtime_gitignored": True,
        "contains_story_text": False,
        "scenes": scenes,
    }
    _write_json(runtime_dir / "package.public.json", result)
    _write_json(PUBLIC_MANIFEST, result)
    return result


class _MemoryBlackboard:
    def __init__(self, task_id: str, source: dict[str, Any]) -> None:
        self.task_id = task_id
        self.values = {
            "event_graph": copy.deepcopy(source["task"].get("event_graph", [])),
            "world_state": copy.deepcopy(source["task"].get("world_state", {})),
            "status": "writing",
            "outline": copy.deepcopy(source["outline"]),
        }

    def get(self, task_id, key): return copy.deepcopy(self.values.get(key))
    def set(self, task_id, key, value): self.values[key] = copy.deepcopy(value)
    def xadd_event(self, *args, **kwargs): return "0-0"
    def save_checkpoint(self, *args, **kwargs): return None
    def load_checkpoint(self, *args, **kwargs): return None


class _FrozenVectorStore:
    def __init__(self, scenes: list[dict[str, Any]]) -> None:
        self.by_query = {item["query"]: copy.deepcopy(item["rag_items"]) for item in scenes}
        self._last_search_trace = {}

    def search_with_meta(self, query, k=5, task_id=None, candidate_k=None):
        items = copy.deepcopy(self.by_query.get(query, []))[:k]
        self._last_search_trace = {
            "query": query, "filter": {"task_id": "frozen_field_trial"},
            "elapsed_ms": 0.0, "candidate_count": len(items),
            "returned_count": len(items), "candidates": [],
        }
        return items

    @property
    def last_search_trace(self): return copy.deepcopy(self._last_search_trace)
    def add_text(self, **kwargs): return None
    def enforce_task_limit(self, task_id): return 0


class _SharedAuxLLM:
    def __init__(self, real, style_behavior: str, handover_brief: str) -> None:
        self.real = real
        self.style_behavior = style_behavior
        self.handover_brief = handover_brief

    def chat_completion(self, messages, **kwargs):
        prompt_name = kwargs.get("prompt_name")
        if prompt_name == "style_behavior": return self.style_behavior
        if prompt_name == "handover_brief": return self.handover_brief
        raise RuntimeError("unexpected auxiliary LLM call in controlled field trial")


def _inject_scene_spec(messages: list[dict[str, str]], scene_text: str) -> list[dict[str, str]]:
    result = copy.deepcopy(messages)
    user_index = max(index for index, item in enumerate(result) if item.get("role") == "user")
    result[user_index]["content"] += SCENE_HEADER + scene_text
    return result


def run_trial(runtime_dir: Path, *, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise RuntimeError("run requires --confirm-private-inputs")
    package = _read_json(runtime_dir / "package.private.json")
    public = _read_json(runtime_dir / "package.public.json")
    if settings.LLM_MODEL != public["model"]:
        raise RuntimeError("configured model differs from prepared package")
    source = package["source"]
    real_llm = get_llm_client()
    style_prompt = STYLE_BEHAVIOR_PROMPT.format(
        style_params=json.dumps(source["checkpoint"].get("style_profile", {}), ensure_ascii=False, indent=2)
    )
    style_behavior = real_llm.chat_completion(
        [{"role": "user", "content": style_prompt}], temperature=0.3,
        max_tokens=600, prompt_name="phase4r_final_shared_style_behavior",
    )
    handovers = source["checkpoint"].get("_prev_handover") or []
    handover = handovers[-1] if handovers else {}
    handover_brief = real_llm.chat_completion(
        [{"role": "system", "content": "你是一位小说编辑。请输出交接简报。"},
         {"role": "user", "content": HANDOVER_BRIEF_PROMPT.format(handover_json=json.dumps(handover, ensure_ascii=False, indent=2))}],
        temperature=0.3, max_tokens=300, prompt_name="phase4r_final_shared_handover",
    ) if handover else "（这是第一节，无前文交接笔记）"

    from app.agents.writer import Writer
    from app.narrative_event import EventGraph
    from app.world_state import WorldStateManager

    scene_by_sub = {int(item["subsection"].get("subsection", 0)): item for item in package["scenes"]}
    outputs: dict[str, dict[int, dict[str, Any]]] = {arm: {} for arm in ARMS}

    class TrialWriter(Writer):
        def __init__(self, arm: str):
            self.llm = _SharedAuxLLM(real_llm, style_behavior, handover_brief)
            self.arm = arm
            self.last_raw_response = ""

        def _generate_with_retry(self, messages, call_max_tokens, stream_callback, section_num, sub_num, mandatory_events_text, **kwargs):
            scene = scene_by_sub[sub_num]
            actual = _inject_scene_spec(messages, scene["scene_spec_text"]) if self.arm == "legacy_full_scene_spec" else copy.deepcopy(messages)
            started = time.perf_counter()
            text = real_llm.chat_completion(
                actual, temperature=0.5, max_tokens=call_max_tokens, max_retries=0,
                top_p=0.9, prompt_name="phase4r_final_real_writing_ab",
            )
            elapsed = round((time.perf_counter() - started) * 1000, 3)
            digest = _sha256_text(text)
            outputs[self.arm][sub_num] = {
                "text": text, "output_sha256": digest, "messages_hash": messages_hash(actual),
                "input_tokens": messages_tokens(actual), "elapsed_ms": elapsed,
                "scene_spec_hash": scene["scene_spec"]["spec_hash"] if self.arm == "legacy_full_scene_spec" else None,
            }
            self._last_generation_artifact = GenerationArtifact(
                raw_output=text, draft=text, generation_attempts=[{"reason": "initial", "temperature": 0.5, "output_chars": len(text)}],
                finish_reason="generated", latency_ms=elapsed, output_hash=digest,
            )
            return text

        def _adjust_generated_length(self, draft, **kwargs):
            return GenerationArtifact(
                raw_output=draft, draft=draft, generation_attempts=[], finish_reason="field_trial_no_post_generation_rewrite",
                latency_ms=0.0, output_hash=_sha256_text(draft),
            )

        def _extract_handover(self, *args, **kwargs): return None

    frozen = source["frozen_contexts"]
    for arm in ARMS:
        board = _MemoryBlackboard(source["task_id"], source)
        events = EventGraph(board, source["task_id"])
        world = WorldStateManager(board, source["task_id"], event_graph=None)
        writer = TrialWriter(arm)
        with ExitStack() as stack:
            stack.enter_context(patch("app.agents.writer.rule_store.build_rules_context", return_value=frozen["rules"]))
            stack.enter_context(patch("app.agents.writer.foreshadowing_store.build_foreshadowing_context", return_value=frozen["foreshadowing"]))
            stack.enter_context(patch("app.faction_store.build_faction_context", return_value=frozen["factions"]))
            stack.enter_context(patch("app.map_manager.build_location_context", return_value=frozen["locations"]))
            stack.enter_context(patch("app.item_manager.build_item_context", return_value=frozen["items"]))
            stack.enter_context(patch("app.character_relation_store.extract_relations_from_text", return_value=[]))
            stack.enter_context(patch.object(settings, "WRITER_REVIEW_TRIGGER_SUBS", 999))
            stack.enter_context(patch.object(settings, "WRITER_REVIEW_TRIGGER_CHARS", 10**9))
            stack.enter_context(patch.object(settings, "RAG_PHASE3_SHADOW", False))
            stack.enter_context(patch.object(settings, "WRITER_BOUNDARY_VALIDATOR_SHADOW", False))
            writer.run(
                topic=source["checkpoint"].get("config_topic", ""),
                style=source["checkpoint"].get("style_profile", {}),
                outline=copy.deepcopy(source["outline"]), vector_store=_FrozenVectorStore(package["scenes"]),
                blackboard=board, task_id=source["task_id"],
                characters=source["checkpoint"].get("characters", []),
                character_arcs=copy.deepcopy(source["checkpoint"].get("character_arcs", [])),
                world_setting=source["checkpoint"].get("config_world_setting", ""),
                prev_draft=source["checkpoint"].get("_prev_draft", ""),
                prev_handover_list=source["checkpoint"].get("_prev_handover", []),
                world_state=world, event_graph=events,
                rules_context=frozen["rules"], subplot_context=frozen["subplots"],
                relation_context=frozen["relations"], experience_context=frozen["experience"],
                narrative_beats=source["checkpoint"].get("narrative_beats"),
                reference_text=source["checkpoint"].get("config_reference_text", ""),
            )
    private_mapping, queries = {}, []
    for index in range(1, 5):
        order = list(ARMS)
        random.Random(47000 + index).shuffle(order)
        candidates = []
        mapping = {}
        qdir = runtime_dir / f"scene_{index:02d}"
        qdir.mkdir(parents=True, exist_ok=True)
        for position, arm in enumerate(order, 1):
            candidate_id = f"candidate_{position:02d}"
            item = outputs[arm][index]
            (qdir / f"{candidate_id}.txt").write_text(item["text"], encoding="utf-8")
            candidates.append({
                "candidate_id": candidate_id, "output_sha256": item["output_sha256"],
                "characters": len(item["text"]), "input_tokens": item["input_tokens"],
            })
            mapping[candidate_id] = {key: value for key, value in item.items() if key != "text"} | {"arm": arm}
        _write_json(qdir / "blind.json", {"trial_index": index, "candidates": candidates})
        private_mapping[str(index)] = mapping
        queries.append({"trial_index": index, "candidates": candidates})
    _write_json(runtime_dir / "arm_mapping.private.json", private_mapping)
    run_manifest = {
        "schema_version": "phase4r-final-field-trial-run-v1", "status": "generated_awaiting_user_review",
        "main_writer_calls": 8, "shared_auxiliary_calls": 2 if handover else 1,
        "arms": list(ARMS), "queries": queries,
    }
    _write_json(runtime_dir / "run_manifest.json", run_manifest)
    build_review_template(runtime_dir)
    return run_manifest


def build_review_template(runtime_dir: Path) -> dict[str, Any]:
    manifest = _read_json(runtime_dir / "run_manifest.json")
    template = {
        "schema_version": "phase4r-final-field-trial-user-review-v1",
        "review_provenance": "user_real_writing_acceptance",
        "instructions": (
            "Read candidates blind and fill every required quality field without opening "
            "arm_mapping.private.json. edit_characters and edit_minutes may remain null "
            "when edit cost was not measured."
        ),
        "scenes": [],
    }
    for scene in manifest["queries"]:
        candidates = [item["candidate_id"] for item in scene["candidates"]]
        template["scenes"].append({
            "trial_index": scene["trial_index"],
            "preference": None,
            "better_continuation_candidate": None,
            "positive_effect_note": "",
            "candidates": [{
                "candidate_id": candidate,
                "goal_complete": None, "hard_violation": None, "relationship_violation": None,
                "continuity_error": None, "fact_error": None, "event_order_error": None,
                "crossed_stop_boundary": None, "edit_characters": None, "edit_minutes": None,
                "review_note": "",
            } for candidate in candidates],
        })
    _write_json(runtime_dir / "user_review.template.json", template)
    return template


def import_results(source_dir: Path, runtime_dir: Path) -> dict[str, Any]:
    expected = _read_json(runtime_dir / "package.public.json")
    actual = _read_json(source_dir / "package.public.json")
    if expected["source_snapshot_sha256"] != actual["source_snapshot_sha256"]:
        raise AssertionError("source snapshot mismatch")
    copied = []
    for name in ("run_manifest.json", "arm_mapping.private.json", "user_review.template.json"):
        shutil.copy2(source_dir / name, runtime_dir / name)
        copied.append(name)
    for index in range(1, 5):
        source_scene, target_scene = source_dir / f"scene_{index:02d}", runtime_dir / f"scene_{index:02d}"
        target_scene.mkdir(parents=True, exist_ok=True)
        for path in source_scene.glob("*"):
            shutil.copy2(path, target_scene / path.name)
    return {"status": "imported_awaiting_user_review", "files": copied, "scene_count": 4}


def evaluate_trial(runtime_dir: Path, review_path: Path) -> dict[str, Any]:
    review = _read_json(review_path)
    mapping = _read_json(runtime_dir / "arm_mapping.private.json")
    if review.get("review_provenance") != "user_real_writing_acceptance":
        raise ValueError("final trial requires user_real_writing_acceptance provenance")
    error_fields = (
        "hard_violation", "relationship_violation", "continuity_error",
        "fact_error", "event_order_error", "crossed_stop_boundary",
    )
    by_arm = {
        arm: {
            "edits": [], "minutes": [], "fully_measured_edits": 0,
            "errors": {field: 0 for field in error_fields}, "goals": 0,
        }
        for arm in ARMS
    }
    b_not_worse = 0
    positive = 0
    public_scenes = []
    for scene in review["scenes"]:
        index = str(scene["trial_index"])
        reverse = {candidate: data["arm"] for candidate, data in mapping[index].items()}
        preference = scene["preference"]
        if preference not in {*reverse, "tie", "both_unusable"}:
            raise ValueError("invalid blind preference")
        continuation = scene.get("better_continuation_candidate")
        if continuation not in {*reverse, "tie", "both_unusable"}:
            raise ValueError("invalid continuation preference")
        preferred_arm = reverse.get(preference, preference)
        if preferred_arm in {"legacy_full_scene_spec", "tie"}:
            b_not_worse += 1
        if preferred_arm == "legacy_full_scene_spec" and scene.get("positive_effect_note", "").strip():
            positive += 1
        for candidate in scene["candidates"]:
            if any(candidate.get(field) is None for field in (
                "goal_complete", "hard_violation", "relationship_violation", "continuity_error",
                "fact_error", "event_order_error", "crossed_stop_boundary",
            )):
                raise ValueError("review contains incomplete candidate fields")
            if candidate.get("review_note") is None or not isinstance(candidate.get("review_note"), str):
                raise ValueError("review contains incomplete candidate fields")
            arm = reverse[candidate["candidate_id"]]
            edit_characters = candidate.get("edit_characters")
            edit_minutes = candidate.get("edit_minutes")
            for field, value in (("edit_characters", edit_characters), ("edit_minutes", edit_minutes)):
                if value is not None and (
                    isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
                ):
                    raise ValueError(f"{field} must be null or a non-negative number")
            if edit_characters is not None:
                by_arm[arm]["edits"].append(float(edit_characters))
            if edit_minutes is not None:
                by_arm[arm]["minutes"].append(float(edit_minutes))
            if edit_characters is not None and edit_minutes is not None:
                by_arm[arm]["fully_measured_edits"] += 1
            by_arm[arm]["goals"] += int(bool(candidate["goal_complete"]))
            for field in error_fields:
                by_arm[arm]["errors"][field] += int(bool(candidate[field]))
        public_scenes.append({"trial_index": int(index), "preferred_arm": preferred_arm})
    expected_edit_samples = len(review["scenes"])
    summary = {}
    for arm, data in by_arm.items():
        measured_values = len(data["edits"]) + len(data["minutes"])
        if measured_values == 0:
            edit_cost_status = "not_measured"
        elif len(data["edits"]) == expected_edit_samples and len(data["minutes"]) == expected_edit_samples:
            edit_cost_status = "fully_measured"
        else:
            edit_cost_status = "partially_measured"
        summary[arm] = {
            "edit_cost_status": edit_cost_status,
            "measured_edit_samples": data["fully_measured_edits"],
            "measured_edit_character_samples": len(data["edits"]),
            "measured_edit_minute_samples": len(data["minutes"]),
            "average_edit_characters": round(mean(data["edits"]), 2) if data["edits"] else None,
            "average_edit_minutes": round(mean(data["minutes"]), 2) if data["minutes"] else None,
            "goal_complete_count": data["goals"],
            "errors": data["errors"],
            "error_count": sum(data["errors"].values()),
        }
    gates = {
        "b_not_worse_at_least_3_of_4": b_not_worse >= 3,
        "b_no_new_hard_violations": (
            summary["legacy_full_scene_spec"]["errors"]["hard_violation"]
            <= summary["legacy_full"]["errors"]["hard_violation"]
        ),
        "b_no_new_relationship_violations": (
            summary["legacy_full_scene_spec"]["errors"]["relationship_violation"]
            <= summary["legacy_full"]["errors"]["relationship_violation"]
        ),
        "b_no_new_fact_errors": (
            summary["legacy_full_scene_spec"]["errors"]["fact_error"]
            <= summary["legacy_full"]["errors"]["fact_error"]
        ),
        "b_no_increase_in_continuity_event_order_or_boundary_errors": all(
            summary["legacy_full_scene_spec"]["errors"][field]
            <= summary["legacy_full"]["errors"][field]
            for field in ("continuity_error", "event_order_error", "crossed_stop_boundary")
        ),
        "b_goal_completion_not_lower": (
            summary["legacy_full_scene_spec"]["goal_complete_count"]
            >= summary["legacy_full"]["goal_complete_count"]
        ),
        "positive_effect_in_at_least_2_scenes": positive >= 2,
    }
    keep = all(gates.values())
    result = {
        "schema_version": "phase4r-final-field-trial-evaluation-v1",
        "status": "completed_phase4r_closed", "review_provenance": review["review_provenance"],
        "scene_count": 4, "main_writer_calls": 8, "summary": summary,
        "b_not_worse_count": b_not_worse, "positive_effect_scene_count": positive,
        "gates": gates, "keep_scene_spec_route": keep,
        "decision": "retain_scene_spec_experimental_route" if keep else "close_phase4r_no_further_batches",
        "scenes": public_scenes,
        "private_prose_emitted": False,
    }
    _write_json(runtime_dir / "evaluation.private.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("snapshot", "prepare", "run", "import", "evaluate"))
    parser.add_argument("--task-id")
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--confirm-private-inputs", action="store_true")
    args = parser.parse_args()
    if args.command == "snapshot":
        if not args.task_id: parser.error("snapshot requires --task-id")
        result = snapshot_task(args.task_id, args.runtime_dir)
    elif args.command == "prepare":
        result = prepare_trial(args.runtime_dir)
    elif args.command == "run":
        result = run_trial(args.runtime_dir, confirmed=args.confirm_private_inputs)
    elif args.command == "import":
        if not args.source_dir: parser.error("import requires --source-dir")
        result = import_results(args.source_dir, args.runtime_dir)
    else:
        if not args.review: parser.error("evaluate requires --review")
        result = evaluate_trial(args.runtime_dir, args.review)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
