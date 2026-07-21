# Writer first-draft execution contract

Date: 2026-07-21

## Outcome

The deterministic `WriterExecutionContract` is integrated behind `WRITER_EXECUTION_CONTRACT_MODE`, which defaults to `off`. No Writer or LLM call was made during implementation. No real canary result exists yet, and this report does not authorize a general production switch.

The only canary behavior change is appending one compact execution block to the final user message after `PromptBuilder` and before `GenerationController`. The legacy context, RAG order, system prompt, model parameters, retry policy, Mandatory Event detector, Condense mode, checkpoint, Character State propagation, and final Review remain unchanged.

## Fixed baseline

The latest real task `92e1adcc-1ae5-4e68-932c-b718ec1a9217` generated four first drafts against targets of approximately 1,000 characters:

| Subsection | Characters | Target ratio |
|---|---:|---:|
| 1 | 1,124 | 1.124 |
| 2 | 2,399 | 2.399 |
| 3 | 1,720 | 1.720 |
| 4 | 4,273 | 4.273 |

The task completed with one main draft call per subsection and zero Mandatory Event retries. This baseline motivates the canary but is not reused as a synthetic evaluation set.

## Contract

The compiler reuses the existing `OutlineSceneSpecProvider`, subsection description, ordered mandatory-event sources, and current `target_words` character-count behavior. It does not create a second planner or state store.

The rendered view contains:

- the current subsection objective;
- existing required events in stable order;
- confirmed continuity and unknown/prohibited facts already represented by SceneSpec;
- the next-subsection stop boundary;
- a soft 85%-130% character range.

More than five required events are retained and marked `overplanned_contract`; they are never silently truncated. The complete rendered contract is capped at 450 estimated tokens.

## Modes and fallback

- `off`: no build, injection, or observation.
- `shadow`: build and record hashes, without changing messages.
- `canary`: append one execution block; do not also inject the full SceneSpec.

Missing or invalid outline data, an untraceable manifest, an empty contract, token overflow, unstable hash, or compiler error returns the original `PromptArtifact`. Fallback does not block Writer, trigger retry, alter checkpoint state, or inject a partial contract.

Observability stores hashes, counts, ratios, source IDs, mode, timing, and fallback reasons. It does not store prose, Prompt, messages, SceneSpec source text, credentials, database content, or Chroma content.

## Scope and stop rule

Engineering verification uses targeted tests only. The next authorized action is at most one normal four-subsection canary on a dedicated local Worker. No A/B package, old Q4/Q6/Q7/Q8 replay, threshold grid, Validator, Repair, Character Arc change, RAG change, ContextManager change, Phase 5, or Phase 6 is authorized.

After that single canary, the route must be retained or closed. It must not be expanded into additional experimental batches.

## Real canary closure (2026-07-22)

The single authorized real canary completed, but V1 injected only S1.3 and S1.4; S1.1/S1.2 exceeded the 450-token contract cap. Successful injection did not control length: S1.3 produced 1,592 characters and S1.4 produced 3,095 against a 1,000-character target.

V1.1 adds fail-safe attempted-token/component observability, normalized exact rendered-list deduplication, an explicit whole-subsection length instruction and a final immediate-stop instruction. It does not change typed semantics, source provenance or the 450-token cap. Offline reconstruction yields 888/579/496/339 tokens, so only S1.4 can inject. The S1.3/S1.4 semantic hashes remain unchanged.

Status is now `v11_engineering_complete_not_eligible_for_demo`. Production remains `off`; no additional real demo is authorized. The next independent direction is outline event-density auditing.

## Canary command

```cmd
set WRITER_EXECUTION_CONTRACT_MODE=canary
set WRITER_INCREMENTAL_SECTION_REVIEW=false
set WRITER_CONDENSE_MODE=warn
uv run celery -A app.celery_app worker --loglevel=info -P solo -Q writing
```

To restore the production default:

```cmd
set WRITER_EXECUTION_CONTRACT_MODE=off
```
