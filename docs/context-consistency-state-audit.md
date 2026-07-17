# Context consistency state audit

Date: 2026-07-17  
Scope: Phase 1 ownership and overlap audit. This document describes the current code after Batch A; it is not a target-architecture claim.

## Ownership decisions

| State | Authoritative owner | Persistence | Compatibility/read models | Known gap |
|---|---|---|---|---|
| Stable character profile | `CharacterProfile` / `CharacterStore` | `characters.db` | Writer `CharacterFormatter` | Dynamic state is still mixed into `CharacterArc.current_state`; a dedicated versioned `CharacterState` store does not yet exist. |
| Dynamic character state | Target owner: `CharacterState` contract | Currently Blackboard/checkpoint via `character_arcs` | `CharacterManager.update_states` | Not yet a single durable authority. Phase 7 must migrate this before claiming closure. |
| Character relationship | `character_relation_store` | `character_relations.db` | `build_relation_context` | Extracted updates have source section but no source chunk/version. |
| Objective world fact | `WorldStateManager` | task-scoped WorldState DB/Blackboard snapshot | Writer world-fact block | Handover `new_facts` is an extraction path, not a second owner. |
| Occurred/planned narrative event | `event_store` | `events.db` | `EventGraph`, deprecated `experience_timeline` API | Existing graph edges remain untyped/undirected and must not be described as proven causality. |
| Original prose evidence | `VectorStore` StoryChunk | Chroma `writing_paragraphs` | `search_with_meta` | Legacy chunks lack complete source version/hash metadata; new chunks include it. |
| Recent continuity handoff | Writer handover chain | Blackboard/checkpoint/task archive | `Writer._build_handover_brief` | May overlap recent raw context; Context Broker must later deduplicate. |
| User writing rules | `rule_store` | `rules.db` | Writer unified rules block | Rule source version is not yet emitted as `ContextItem`. |
| Foreshadowing lifecycle | `foreshadowing_store` | `foreshadowings.db` | Writer unified rules block | Evidence chunk IDs are not yet mandatory. |
| Style target | `StyleProfile` (4 primary controls plus compatibility fields) | task configuration/Blackboard | `StyleSummarizer`, Writer | Historical “50 dimension” documents are superseded; objective post-write measurements live in `style_stats`/quality fixtures. |

## Module inventory

| Module/mechanism | Main callers | Input → output | Persistence | Failure fallback | Enters Writer prompt |
|---|---|---|---|---|---|
| `agents/context_manager.py` | Writer | completed subsection → last 3 raw subsections | checkpoint `context_state` | No LLM; deterministic buffer | Yes, `summary_context` (name is legacy) |
| Handover extraction | Writer | subsection prose → structured continuity notes | Blackboard/checkpoint/archive | warning + empty handover | Yes, latest handover brief |
| `world_state.py` | Coordinator, Writer | extracted facts/query terms → relevant facts/warnings | world-state store + snapshot | warning + skip relevant facts | Yes |
| `event_store.py` | Coordinator, Experience compatibility | planned/extracted event → canonical event rows | `events.db` | caller logs and skips | Yes, through EventGraph/experience context |
| `experience_timeline.py` | Coordinator, `/api/experience` | section prose/event query → canonical event-store view | none of its own | warning + empty extraction | Yes, long-term experience context |
| `narrative_event.EventGraph` | Coordinator, Writer | milestones/section query → ranked/expanded events | `events.db` graph tables | warning + semantic-only retrieval | Yes |
| `vector_store.py` | Coordinator, Writer, deprecated MemoryFuser | StoryChunk/query → chunks plus trace metadata | Chroma | empty/duplicate writes skipped with provenance log | Yes |
| `character_store.py` | Character routes/manager | profile CRUD → stable profile | `characters.db` | schema migration warning + existing schema | Yes through character formatting |
| `character_relation_store.py` | Writer, relationship routes | prose/relations → current relationship state | `character_relations.db` | extraction currently returns empty on parse/call failure | Yes |
| `rule_store.py` | Writer/rule routes | user rules → enabled rule context | `rules.db` | caller logs/continues | Yes |
| `foreshadowing_store.py` | Writer/coordinator/routes | foreshadowing records → due/current constraints | `foreshadowings.db` | warning + skip | Yes |
| `memory_fuser.py` | No production caller found by repository-wide search | multiple memories → fused text | none | warning + omit RAG | No; deprecated prototype |

## Overlap matrix

Legend: **O** authoritative owner, **R** read/compatibility view, **E** evidence, **D** duplicate risk.

| Mechanism | Recent prose | Stable profile | Dynamic state | Relationship | World fact | Planned/occurred event | Prompt continuity |
|---|---:|---:|---:|---:|---:|---:|---:|
| Handover | R |  | R | R | extraction input | R | O |
| ContextManager recent buffer | E |  |  |  |  |  | R |
| WorldState |  |  |  |  | O | R | R |
| Character profile/store |  | O |  |  |  |  | R |
| CharacterArc/checkpoint |  |  | D (temporary) |  |  | planned milestones | R |
| CharacterRelation store |  |  |  | O |  | extracted trigger | R |
| EventStore/EventGraph |  |  |  |  | world-fact compatibility | O | R |
| ExperienceTimeline |  |  |  |  |  | R | R |
| Chroma StoryChunk | E | E | E | E | E | E | R |
| MemoryFuser | D | D | D |  |  | D | D |

## Writer context source mapping

Every current Writer prompt block maps to one owner or an explicit evidence/read model:

| Writer block | Source |
|---|---|
| global/section rules | `rule_store` + `StorylineConstraint` |
| foreshadowing | `foreshadowing_store` |
| factions/map/items | their domain stores; soft context only |
| character profile | request/`CharacterStore` formatted by `CharacterFormatter` |
| character arc/current state | temporary Blackboard `character_arcs` compatibility state |
| relationship | `character_relation_store` |
| world facts | `WorldStateManager` |
| ranked events | `EventGraph`/`event_store` |
| recent text | `ContextManager` raw buffer |
| handover | Writer handover chain |
| semantic evidence | Chroma StoryChunk via `VectorStore` |
| style | task `StyleProfile` + deterministic summarizer/examples |

The temporary dynamic-character-state exception is explicit: Phase 1 defines the target owner but does not pretend the persistence migration is complete.

## Deprecated and retained artifacts

- `MemoryFuser`: no production caller; marked deprecated. It was not deleted because the file already contained uncommitted user edits.
- `ExperienceTimeline`: retained as an API/import compatibility module. It no longer owns `experience.db` writes or reads; `event_store` is canonical.
- `experience.db`: retained on disk as a recoverable legacy backup. No automatic destructive migration or deletion was performed.
- Historical 50-dimension style design/debug documents: retained for history and marked superseded where appropriate. Active README/API descriptions use the implemented 4 primary controls.

## Next audit gates

Before Phase 3/4 can claim a unique state architecture:

1. Introduce a versioned, evidence-linked `CharacterState` persistence owner.
2. Emit current sources as `ContextItem` records with source IDs/versions.
3. Replace EventGraph’s co-occurrence links with directed typed `EventEdge` only after evidence annotation.
4. Measure Handover vs recent-buffer duplication in Context Broker shadow mode before removing either source.
