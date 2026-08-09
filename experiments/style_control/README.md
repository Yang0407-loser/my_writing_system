# Style-control route experiment

This package runs an isolated A/B/C/D comparison without changing the
production Writer or enabling the handover lifecycle.

## Arms

- A: no style input.
- B: current four-control profile plus its unpolished natural-language brief.
- C: the recovered historical "50D" analysis translated into a natural-language
  brief. The initial class actually contains 49 non-metadata controls; the
  experiment records that discrepancy.
- D: stable work-level style contract, positive/negative examples and a small
  scene modulation.

The current production Writer mixes four-dimensional labels, an LLM-generated
behavior instruction, template examples and raw-reference few-shot passages.
This runner intentionally separates those signals so an observed effect can be
attributed to one arm.

## Offline pipeline

```powershell
python -m experiments.style_control.runner plan
python -m experiments.style_control.runner control-plan
python -m experiments.style_control.runner run --backend mock
python -m experiments.style_control.runner run --backend mock --plan-file control_run_manifest.json
python -m experiments.style_control.runner metrics
python -m experiments.style_control.runner control-metrics
python -m experiments.style_control.runner anonymise
python -m experiments.style_control.runner estimate
```

Mock completions validate persistence, resume, metrics and blind-review
packaging only. Their JSON records carry `route_evidence=false`; they must never
be used to select a production route.

## Real preparation and generation

Prepare all three style inputs once:

```powershell
python -m experiments.style_control.runner prepare `
  --backend llm `
  --output outputs/style-control-experiment-real/prepared-style-inputs.json
```

Build and inspect the plan before spending tokens:

```powershell
python -m experiments.style_control.runner plan `
  --prepared outputs/style-control-experiment-real/prepared-style-inputs.json `
  --run-dir outputs/style-control-experiment-real
python -m experiments.style_control.runner control-plan `
  --run-dir outputs/style-control-experiment-real
python -m experiments.style_control.runner estimate `
  --run-dir outputs/style-control-experiment-real
```

After explicit cost approval:

```powershell
python -m experiments.style_control.runner run `
  --backend llm `
  --run-dir outputs/style-control-experiment-real
```

Run the separately planned single-variable matrix with:

```powershell
python -m experiments.style_control.runner run `
  --backend llm `
  --run-dir outputs/style-control-experiment-real `
  --plan-file control_run_manifest.json
python -m experiments.style_control.runner metrics `
  --run-dir outputs/style-control-experiment-real
python -m experiments.style_control.runner control-metrics `
  --run-dir outputs/style-control-experiment-real
python -m experiments.style_control.runner anonymise `
  --run-dir outputs/style-control-experiment-real
```

Rerun only one failed sample:

```powershell
python -m experiments.style_control.runner run `
  --backend llm `
  --run-dir outputs/style-control-experiment-real `
  --rerun-id S1__SC1__B__r1
```

The runner writes every prompt and result independently and updates
`run_manifest.json` after every call, so a stopped batch resumes without
repeating completed calls.

## Schema-v2 contract ablation

The S3-first D-route causal ablation is isolated from the legacy A/B/C/D
semantics.  It separates global prose rules, StyleSignature,
SceneModulation, StyleDemonstrations and audit-only evidence.

Build and exercise the complete offline/mock package:

```powershell
python -m experiments.style_control.runner contract-ablation mock-all `
  --run-dir outputs/style-contract-ablation
```

The five arms are D0 contract-only, D1 contract+positive, D2
contract+negative, D3 all components and diagnostic-only F0 positive
few-shot without a signature.  Unsafe demonstrations stop planning rather
than merely emitting a warning.

Real calls remain disabled unless the invocation contains the explicit
approval gate:

```powershell
python -m experiments.style_control.runner contract-ablation run `
  --backend llm `
  --enable-real-calls `
  --run-dir outputs/style-contract-ablation
```

Use `--rerun-id` only for a failed sample. Completed samples are preserved
and skipped on resume.
