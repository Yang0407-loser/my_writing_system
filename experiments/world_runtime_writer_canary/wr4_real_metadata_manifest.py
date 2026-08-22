# -*- coding: utf-8 -*-
"""Build the real-WR-metadata manifest for the full book from the commit chain."""
import json
from pathlib import Path
from types import SimpleNamespace

from app.writing.world_runtime_metadata_projection import project_rag_metadata
from app.writing.world_runtime_state_committer import CommittedWorldState
from app.writing.wr_rag_metadata_wiring import flat_rag_metadata


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "experiments/world_runtime_writer_canary/fixtures/wr4_metadata_holdout_corpus_snapshot_v1.json"
COMMITS = ROOT / ".world_runtime_wr4_fullbook_wr_commit_canary_runtime/private/commits"
TASK_ID = "20f02dc7-dc64-4233-bd6c-06a6d8647dbe"
OUTPUT = ROOT / "reports/wr4-fullbook-real-metadata-manifest-v1.json"


def _display_name(subject: str) -> str:
    mapping = {
        "character:lin-wan": "林晚",
        "character:zhou-ye": "周野",
        "character:ji-qing": "季晴",
        "character:coworker": "老吴",
    }
    if subject.startswith("employment:"):
        subject = "character:" + subject.split(":", 1)[1]
    return mapping.get(subject, subject.split(":", 1)[-1])


def flat_from_state(state, *, section, subsection):
    characters = sorted({
        _display_name(fact.subject)
        for fact in state.facts
        if fact.subject.startswith(("character:", "employment:"))
    })
    clock_time = next(
        (fact.value for fact in state.facts if fact.subject == "world_clock" and fact.predicate == "time"),
        None,
    )
    weekday = next(
        (fact.value for fact in state.facts if fact.subject == "world_clock" and fact.predicate == "weekday"),
        None,
    )
    locations = sorted({
        str(fact.value)
        for fact in state.facts
        if fact.subject.startswith("character:") and fact.predicate == "location"
    })
    return {
        "characters": characters,
        "time": clock_time,
        "weekday": weekday,
        "locations": locations,
        "world_revision": state.revision,
        "source": "world_runtime_wr3.5",
        "section": section,
        "subsection": subsection,
        "metadata_source": "world-runtime-metadata-projection-wr3.5-v1",
    }


def main():
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    rows = snapshot["tasks"][TASK_ID]["rows"]
    subsection_rows = {}
    for r in rows:
        key = (int(r["section"]), int(r["subsection"]))
        subsection_rows.setdefault(key, []).append(r)
    keys = sorted(subsection_rows.keys())
    bootstrap = json.loads(
        (
            ROOT / ".world_runtime_state_commit_canary_runtime/c21r10/private/commits/S1.json"
        ).read_text(encoding="utf-8")
    )
    adapt = {
        ("bakery:wild-bread", "opens_at"): "07:00",
        ("world_clock", "time"): "03:30",
    }
    bootstrap_state = SimpleNamespace(
        revision=0,
        facts=[
            SimpleNamespace(
                subject=fact["subject"],
                predicate=fact["predicate"],
                value=adapt.get((fact["subject"], fact["predicate"]), fact["value"]),
            )
            for fact in bootstrap["before"]["facts"]
        ],
    )

    per_subsection = {}
    current_committed = None
    expected_revision = 1
    for key in keys:
        section, subsection = key
        commit_path = COMMITS / f"S{section}_{subsection}.json"
        if commit_path.exists():
            committed = CommittedWorldState.model_validate(
                json.loads(commit_path.read_text(encoding="utf-8"))
            )
            assert committed.after.revision == expected_revision, (
                f"chain break at S{section}.{subsection}: "
                f"expected r{expected_revision}, got r{committed.after.revision}"
            )
            expected_revision += 1
            current_committed = committed
        if current_committed is not None:
            metadata = flat_rag_metadata(
                current_committed, section=section, subsection=subsection
            )
        else:
            metadata = flat_from_state(bootstrap_state, section=section, subsection=subsection)
        per_subsection[key] = metadata

    manifest = {}
    for key, rows_in_sub in subsection_rows.items():
        metadata = per_subsection[key]
        for r in rows_in_sub:
            manifest[r["content_hash"]] = metadata

    characters_seen = sorted({
        name
        for meta in per_subsection.values()
        for name in meta["characters"]
    })
    locations_seen = sorted({
        loc
        for meta in per_subsection.values()
        for loc in meta["locations"]
    })
    times_seen = sorted({
        meta["time"]
        for meta in per_subsection.values()
        if meta["time"] is not None
    })
    report = {
        "schema_version": "wr4-fullbook-real-metadata-manifest-v1",
        "task_id": TASK_ID,
        "source": "world_runtime_wr3.5 (real WR commit chain, fullbook canary)",
        "committed_subsections": [
            [s, ss] for (s, ss) in keys if (COMMITS / f"S{s}_{ss}.json").exists()
        ],
        "final_revision": current_committed.after.revision if current_committed else None,
        "characters_seen": characters_seen,
        "locations_seen": locations_seen,
        "times_seen": times_seen,
        "per_subsection": {
            f"S{s}_{ss}": meta
            for (s, ss), meta in sorted(per_subsection.items())
        },
        "manifest": manifest,
        "manifest_count": len(manifest),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "manifest_count": len(manifest),
                "committed_subsections": report["committed_subsections"],
                "final_revision": report["final_revision"],
                "characters_seen": characters_seen,
                "locations_seen": locations_seen,
                "times_seen": times_seen,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
