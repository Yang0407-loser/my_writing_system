"""Recovered original Writer inputs for the four baseline subsections.

The fixture snapshot was copied from the Redis checkpoint of task
487da043-b11f-4d91-805a-2db132d54955 (the task whose output matches the
baseline A draft) plus the tasks.db world_state_json / events_json columns.

No input is fabricated from the baseline prose: every field below is the raw
value that was actually passed to the Writer for that task.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_FIXTURE_STATE = "fixtures/baseline_task_state.json"
_FIXTURE_WORLD_EVENTS = "fixtures/baseline_world_events.json"

BASELINE_TASK_ID = "487da043-b11f-4d91-805a-2db132d54955"


@dataclass
class ExperimentInputs:
    task_id: str
    topic: str
    style: dict
    outline: list[dict]
    characters: list[dict]
    character_arcs: list[dict]
    world_setting: str
    story_synopsis: str
    reference_text: str
    target_words: int
    narrative_beats: list[dict]
    rules_context: str
    world_facts: list[dict]
    events: list[dict]
    allowed_names: list[str] = field(
        default_factory=lambda: ["林晚", "周野", "顾衍", "季晴", "吴阿姨"]
    )
    known_context: str = (
        "野面包位于林晚居住的老小区附近，只在周六营业，周野周六凌晨三点半开始揉面。"
    )

    @property
    def sections(self) -> list[dict]:
        return self.outline

    def subsection(self, section: int, sub_num: int) -> dict:
        sec = next(s for s in self.outline if s.get("section") == section)
        return next(s for s in sec.get("subsections", []) if s.get("subsection") == sub_num)

    def subsection_target_words(self, section: int, sub_num: int) -> int:
        return self.subsection(section, sub_num).get("target_words", 2000)


def _load(path: str) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_experiment_inputs(
    base_dir: Path | None = None,
    *,
    rules_context: str = "",
) -> ExperimentInputs:
    """Load the recovered original inputs from the fixtures directory."""
    base = Path(base_dir) if base_dir is not None else Path(__file__).parent
    state = _load(base / _FIXTURE_STATE)
    world_events = _load(base / _FIXTURE_WORLD_EVENTS)

    if "api_key" in state:
        # Never carry secrets into prompt snapshots or reports.
        state = {k: v for k, v in state.items() if k != "api_key"}

    return ExperimentInputs(
        task_id=BASELINE_TASK_ID,
        topic=str(state.get("config_topic", "")),
        style=dict(state.get("config_style_profile") or {}),
        outline=list(state.get("config_outline") or []),
        characters=list(state.get("characters") or []),
        character_arcs=[],
        world_setting=str(state.get("config_world_setting", "")),
        story_synopsis=str(state.get("config_story_synopsis", "")),
        reference_text=str(state.get("config_reference_text", "")),
        target_words=int(state.get("config_target_words", 0)),
        narrative_beats=list(state.get("narrative_beats") or []),
        rules_context=rules_context or str(state.get("rules_context", "")),
        world_facts=list(world_events.get("world_facts", [])),
        events=list(world_events.get("events", [])),
    )
