# Roadmap — Generalising `ioctl-cuda-mapping` into a Trace-Driven Virtualization Toolkit

## Date: 2026-04-14

## Purpose

This document describes the target architecture for turning the existing
`ioctl-cuda-mapping` repo (NVIDIA-specific capture + replay) into a
**device-agnostic, trace-driven toolkit** that produces machine-readable
specifications of ioctl-level driver protocols, and consumes those
specifications to drive replay, virtualization stubs, and policy mediation.

This is the concrete engineering backing for the "trace-driven GPU
virtualization" direction in [newdirection.md](newdirection.md) (pushbacks
#2 and #3).

---

## The Main Idea: This Is a Harness, Not a Virtualization Layer

The central reframe — and the thing that distinguishes this work from every
other GPU virtualization project in [newdirection.md](newdirection.md) — is
that we are **not building a GPU virtualization layer**. We are building a
**harness that produces virtualization layers from traces**.

The spec file is the fixed point. The harness's job is to run experiments
until the spec converges, the way a test harness runs until green. A new
driver version is not a code change — it is a re-run of the harness against
a fresh corpus. A new accelerator (AMD, Intel, TPU) is not a rewrite — it
is a new corpus.

Every artifact the project ships — the replay tool, the aegis guest stub,
the host daemon, the reference-monitor policy — is an **emitted product** of
the harness. The handwritten-C surface shrinks as coverage expands, instead
of growing linearly with the driver API.

This is why the repo generalises. Everything below supports this one idea.

### The harness loop

```
   ┌──────────────────────────────────────────────────────────────┐
   │              harness / optimizer / agent                     │
   │                                                              │
   │   pick a program  ──►  run under sniffer  ──►  trace         │
   │         ▲                                        │           │
   │         │                                        ▼           │
   │   mutate inputs                          inference engine    │
   │   / pick next                                    │           │
   │   program                                        ▼           │
   │         ▲                                  spec delta        │
   │         │                                        │           │
   │         │                                        ▼           │
   │    validate: replay                      update spec.json    │
   │    the trace under                               │           │
   │    the new spec ◄────────────────────────────────┘           │
   │         │                                                    │
   │         ▼                                                    │
   │    disagreements / low-confidence fields                     │
   │         │                                                    │
   │         ▼                                                    │
   │    escalate: ask a human (or an LLM)                         │
   │    to resolve → feed decision back into inference priors     │
   │         │                                                    │
   │         ▼                                                    │
   │    optimizer sees diagnostic feedback and proposes           │
   │    the next experiment / heuristic / codegen prompt          │
   └──────────────────────────────────────────────────────────────┘
```

### The five drivers inside the harness

1. **Corpus driver.** A program picker — starts with the CUDA ladder
   (null kernel → one malloc → one memcpy → one launch…). Each rung adds
   one API call. The *diff between rung N and rung N+1* is the signal
   that isolates which ioctls correspond to which operation. Already
   latent in [programs/](ioctl-cuda-mapping/cuda-ioctl-map/programs/) —
   the harness formalises it as "run all, diff adjacent pairs, attribute
   new ioctls."

2. **Mutation driver.** For a fixed program, run N times varying what
   can be varied (allocation sizes, device index, kernel grid dims) to
   generate the within-ioctl variation inference needs to distinguish
   size fields from inline constants. Without mutation, inference has
   one sample per ioctl and can't tell "constant" from "happens to be
   stable."

3. **Validator.** Every spec update is followed by a replay pass: does
   the new spec still replay every existing trace correctly? Regression
   test. The spec is the artifact under test; traces are the test
   corpus.

4. **Escalator.** Inference emits fields with confidence scores
   (§Component Breakdown 2). Low-confidence fields are the harness's
   TODO list. This is where the Claude-workflow angle from
   [newdirection.md](newdirection.md) pushback #2 actually lives: an LLM
   looking at surrounding context (field position, neighbours already
   classified, driver source when available) is good at the semantic
   leap inference can't make — "this 4-byte field after a handle and
   before a size is probably a flags word." Its output is a **prior**,
   not ground truth — the next inference run either confirms it or
   overrides it.

5. **Optimizer.** A GEPA-style `optimize_anything` loop treats the
   harness configuration as a text artifact: corpus schedule, mutation
   policy, inference heuristics, confidence thresholds, prompt templates
   for escalation, and eventually codegen templates. The evaluator is the
   real system: capture traces, infer a spec, replay old and new traces,
   compare against handwritten baselines, and report a multi-objective
   score plus diagnostic feedback. This makes the LLM search targeted:
   it sees why a candidate failed instead of just seeing "score went
   down."

### How GEPA fits

GEPA / `optimize_anything` should not replace the deterministic core of
the project. The sniffer still records bytes, the inference engine still
emits explicit fields with confidence scores, and replay still validates
the resulting spec. GEPA sits **outside** that loop as an experiment
orchestrator and heuristic optimizer.

The artifact GEPA optimizes is text:
- `harness.yaml`: which programs to run, how many repetitions, what
  parameters to mutate, and what budget to spend per phase.
- `infer/heuristics/*.yaml` or Python snippets: thresholds and ordering
  for handle, pointer, fd, size, and output-region classifiers.
- `prompts/*.md`: escalation prompts that turn low-confidence fields into
  proposed semantic annotations.
- Later, `codegen/*.jinja`: templates for guest stubs and host daemons.

The evaluator returns both scores and **Actionable Side Information
(ASI)**. For this repo, ASI is not vague prose; it is exactly the data an
engineer would inspect:
- replay failures with ioctl sequence number, request code, errno, and
  before/after buffer diff;
- spec diffs against the previous candidate and against the handwritten
  `handle_offsets.json` baseline;
- per-field confidence histograms and examples of ambiguous byte ranges;
- coverage by program, device path, ioctl code, and field kind;
- generated stub/daemon compile errors once codegen exists.

GEPA's Pareto frontier is a good fit because there is no single scalar
that captures progress. A candidate that improves pointer classification
but slightly hurts handle recall should survive long enough to be merged
with a candidate that does the opposite. The frontier should track metrics
such as replay success, handle-offset agreement, field coverage, false
positive rate, trace generalization, generated-code compile success, and
runtime overhead.

### Why this framing matters for the research story

- Against AvA (the closest prior art): AvA requires a human-authored
  DSL spec per API. The harness *derives* the spec from traces. That's
  the delta.
- Against rCUDA / gVirtuS: they forward a documented API (1000+ CUDA
  functions, manually wrapped). The harness targets the ~15-ioctl UAPI
  below it and amortises the per-version maintenance cost across every
  consumer.
- Against GVM / Hummingbird / LithOS: orthogonal — they are points in
  the design space this harness can *produce*. The mediator (§5) is how
  a GVM-equivalent policy gets expressed on top of a harness-emitted
  spec.

The components described in the rest of this document are the machinery
that makes this loop run.

---

## Today's Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                     CURRENT (NVIDIA-only)                          │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│   CUDA binary ──► libcuda.so ──► ioctl()                           │
│                         ▲                                          │
│                         │ LD_PRELOAD                               │
│                  nv_sniff.c (hardcoded /dev/nvidia*)               │
│                         │                                          │
│                         ▼                                          │
│                   sniffed/*.jsonl                                  │
│                         │                                          │
│                         ├─► handle_offsets.json  ◄── HANDWRITTEN   │
│                         ├─► ioctl_table.json     ◄── HANDWRITTEN   │
│                         │                                          │
│                         ▼                                          │
│                     replay.py ──► ioctl() ──► kernel               │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

**Limitations:**
- `/dev/nvidia*` hardcoded in the sniffer — won't see `/dev/kfd`, `/dev/accel*`.
- `handle_offsets.json` is human-authored per-ioctl, field-by-field.
- `ioctl_table.json` is a manual code→name map.
- `replay.py` is the only consumer of the captured data, and it hardcodes
  the handle-patching step.
- No way to answer: *"what fields in this ioctl are pointers? sizes? fds?"*
  — only handle offsets are modelled.
- Each new application requires a human to stare at the JSONL and extend
  the offset table.

This is fine for the milestone it was built for (proving the protocol is
re-issuable), but it does not scale to new apps, new driver versions, or
new accelerators.

---

## Target Architecture

```
┌────────────────────────────────────────────────────────────────────────────┐
│                     TARGET (device-agnostic, spec-driven)                  │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│   any accelerator app ──► userspace lib ──► ioctl()                        │
│                                 ▲                                          │
│                                 │ LD_PRELOAD                               │
│              ┌──────── generic ioctl sniffer ────────┐                     │
│              │  (device glob + fd→path tracking)     │                     │
│              │  writes uniform JSONL:                │                     │
│              │    {fd, path, req, sz, before, after, │                     │
│              │     maps_snapshot, fd_table}          │                     │
│              └───────────────────┬───────────────────┘                     │
│                                  │                                         │
│                                  ▼                                         │
│                        traces/<app>.jsonl  (N runs)                        │
│                                  │                                         │
│                                  ▼                                         │
│              ┌─────── schema inference engine ───────┐                     │
│              │  - diff across runs                   │                     │
│              │  - classify each byte range as:       │                     │
│              │      handle | pointer | fd | size |   │                     │
│              │      inline-value | output-region    │                     │
│              │  - confidence score per field         │                     │
│              └───────────────────┬───────────────────┘                     │
│                                  │                                         │
│                                  ▼                                         │
│                    spec/<driver>.spec.json                                 │
│                    (machine-readable per-ioctl schema)                     │
│                                  │                                         │
│                                  ▲                                         │
│              optimizer / evaluator loop                                    │
│              (GEPA-style: propose next corpus,                             │
│               heuristic, prompt, or template)                              │
│                                  │                                         │
│         ┌────────────────────────┼────────────────────────┐                │
│         ▼                        ▼                        ▼                │
│    replay engine         stub/daemon codegen      policy mediator          │
│    (generic              (aegis-style guest/      (reference               │
│     interpreter)          host pair)               monitor)                │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

**Key shift:** the JSON spec file becomes the central artifact. Everything
downstream is a consumer of that spec; everything upstream is a producer.
No consumer has hardcoded per-ioctl knowledge.

---

## Component Breakdown

### 1. Generic Sniffer (`intercept/`)

**Today:** [nv_sniff.c](ioctl-cuda-mapping/cuda-ioctl-map/intercept/nv_sniff.c)
hooks `open`/`ioctl` and filters paths matching `/dev/nvidia*`.

**Target:**
- Device filter becomes a runtime-configurable glob list
  (`NV_SNIFF_DEVICES=/dev/nvidia*:/dev/kfd:/dev/accel*`).
- Per-ioctl record additionally captures:
  - The file path (already tracked via fd→path map — keep it).
  - A snapshot of `/proc/self/maps` at first ioctl on each fd (for
    pointer classification downstream).
  - The caller's open fd table (for fd-reference detection).
- JSONL schema gains optional `path`, `maps_epoch`, `fd_table_epoch`
  fields. Existing fields unchanged — old traces remain replayable.

**Scope creep to avoid:** do not parse struct layouts here. The sniffer
stays dumb — it records bytes and context. All semantics live in the
inference engine.

---

### 2. Schema Inference Engine (`infer/`)

**Today:** [find_handle_offsets.py](ioctl-cuda-mapping/cuda-ioctl-map/tools/find_handle_offsets.py)
diffs two runs to find bytes that change — this detects handles only.

**Target:** extend to a full field classifier. Given N independent runs of
the same program (or N invocations of the same ioctl within one run),
classify each byte range in the `before`/`after` buffers as:

| Classification   | Detection heuristic                                           |
|------------------|---------------------------------------------------------------|
| **handle**       | Changes across runs; values cluster; appears as input in later ioctls. |
| **pointer**      | Value falls inside a range from `/proc/self/maps`.            |
| **fd**           | Small int (< 1024) matching an open fd at capture time.       |
| **size/length**  | Correlates with the length of an adjacent buffer region.      |
| **inline-value** | Stable across runs, or varies without the above signals.      |
| **output-region**| Bytes where `after` differs from `before` in the same call.   |

Output: `spec/<driver>.spec.json`, one entry per ioctl code:

```json
{
  "0xC00846D6": {
    "name": "NV_ESC_CARD_INFO",
    "size": 8,
    "fields": [
      {"off": 0, "len": 4, "kind": "inline", "confidence": 1.0},
      {"off": 4, "len": 4, "kind": "output", "confidence": 0.95}
    ],
    "examples": ["0000008000000000"]
  },
  ...
}
```

**Confidence scores matter.** Low-confidence fields get flagged for
manual review instead of silently guessed.

---

### 3. Replay Engine (`replay/`)

**Today:** [replay.py](ioctl-cuda-mapping/cuda-ioctl-map/replay/replay.py)
hardcodes the handle-patching step and reads `handle_offsets.json`.

**Target:** a thin interpreter over `spec.json`. For each captured
ioctl:
1. Look up the spec entry by cmd code.
2. For each field marked `handle`, patch using the live handle map.
3. For each field marked `pointer`, allocate a fresh buffer in the
   replay process and substitute the address.
4. Issue the ioctl. Record any returned handles in the map.

No per-ioctl branches in the replay code. All knowledge lives in the
spec file.

---

### 4. Stub/Daemon Codegen (`codegen/`) — new

The same spec that drives replay can emit a guest-side kernel-module stub
and a host-side daemon handler for the aegis virtualization system.

For each ioctl in the spec, generate:
- **Guest stub:** flatten fields marked `pointer` into inline buffers,
  emit a vsock message with `{cmd, fields}`, wait for reply.
- **Host daemon:** receive message, allocate buffers for inline regions,
  reconstruct the struct, call real ioctl, serialise any `output-region`
  fields back.

This is the piece that makes aegis maintainable: when a new CUDA version
ships a new ioctl, you capture a trace, run inference, regenerate — no
hand-written C.

---

### 5. Policy Mediator (`mediator/`) — new, longer-term

The reference-monitor role from [newdirection.md §Chosen Direction](newdirection.md).
Sits in the daemon, consumes the same spec, and enforces per-tenant
policy on each ioctl:
- Memory-limit checks on allocation ioctls (by reading `size` fields).
- Redaction / rejection of ioctls the tenant isn't allowed to issue.
- Rate limiting.

Only possible because every field is classified — you can't enforce
"memory limit" without knowing which bytes are sizes.

---

### 6. Optimization Orchestrator (`optimizer/`) — new

This is where the GEPA / `optimize_anything` idea lands.

**Today:** the repo has scripts and tools, but no search loop over
experiment choices. A human chooses which CUDA program to run, which two
traces to diff, and how to update `handle_offsets.json`.

**Target:** an optimizer invokes the existing harness and treats the
result as an evaluator:

```python
def evaluate(candidate, example):
    config = materialize(candidate)
    traces = run_corpus(example.programs, config.mutations)
    spec = infer_spec(traces, config.heuristics)
    replay = replay_all(spec, traces)

    return score(replay, spec), {
        "replay_failures": replay.failures,
        "spec_diff": diff_spec(spec),
        "low_confidence_fields": spec.low_confidence_fields,
        "coverage": coverage_report(spec, traces),
    }
```

Use **multi-task search** first: each CUDA ladder rung is an example, and
the candidate is a shared harness/inference configuration that should
improve across all rungs. Use **generalization** later: train on some
programs and driver versions, then validate on held-out programs or a
new driver version. That is the real evidence that the harness learns
protocol-discovery strategies rather than overfitting to one trace.

**Scope creep to avoid:** do not let GEPA emit the authoritative spec
directly. It proposes harness changes, inference heuristics, prompts, or
templates; the checked-in spec is still produced by deterministic tools
and accepted only after replay validation.

---

## Proposed Directory Layout

```
ioctl-trace-spec/            # renamed repo
├── sniffer/                 # (was intercept/) device-agnostic LD_PRELOAD
│   ├── sniff.c
│   └── Makefile
├── infer/                   # (was tools/find_handle_offsets.py + friends)
│   ├── classify.py
│   ├── heuristics/
│   └── tests/
├── spec/                    # emitted specs, one per driver
│   ├── nvidia-555.spec.json
│   ├── amdgpu.spec.json     # future
│   └── schema.json          # meta-schema
├── replay/                  # generic interpreter
│   └── replay.py
├── optimizer/               # new — GEPA/optimize_anything evaluator loop
│   ├── objective.md
│   ├── harness.yaml
│   ├── evaluate.py
│   └── metrics.py
├── codegen/                 # new — emits aegis stub + daemon
│   ├── stub.c.jinja
│   └── daemon.c.jinja
├── prompts/                 # escalation and semantic-label prompts
│   └── classify_field.md
├── programs/                # test corpus (CUDA ladder, ROCm ladder, …)
│   └── cuda/
├── traces/                  # captured JSONL
└── README.md
```

---

## Migration Plan

Each phase ends in a runnable repo. No big-bang rewrite.

### Phase 0 — Freeze the current API
- Document the existing JSONL schema explicitly.
- Tag the current repo as `v0-nvidia-only` so the rewrite has a stable
  baseline to compare against.

### Phase 1 — Generalise the sniffer
- Replace the `/dev/nvidia*` filter with a glob list.
- Add `path` to every record.
- Verify existing CUDA replay still works byte-for-byte.
- Capture a ROCm program as a smoke test of device-agnosticism
  (replay not required yet).

### Phase 2 — Inference engine v1
- Port [find_handle_offsets.py](ioctl-cuda-mapping/cuda-ioctl-map/tools/find_handle_offsets.py)
  logic into `infer/classify.py`.
- Add pointer detection (requires `/proc/self/maps` snapshot from
  Phase 1's extended sniffer).
- Emit `spec.json` for the existing CUDA ladder.
- **Acceptance test:** the emitted spec reproduces the handwritten
  `handle_offsets.json` entries with ≥95% agreement. Any disagreement
  is either a bug or a latent error in the handwritten table —
  investigate both.

### Phase 2.5 — Optimizer harness v0
- Add `optimizer/evaluate.py` as a thin wrapper around existing commands:
  run a corpus, infer a spec, replay traces, and return structured
  metrics plus ASI-style diagnostics.
- Seed it with a hand-authored `harness.yaml`; do not introduce GEPA
  until the evaluator is deterministic and reproducible.
- First optimized artifact: inference thresholds and corpus schedule,
  not generated C. Keep the blast radius small.
- **Acceptance test:** a candidate is only accepted if it improves at
  least one tracked metric without regressing replay success on the
  baseline CUDA ladder.

### Phase 3 — Generic replay
- Rewrite `replay.py` as a spec interpreter.
- Drop `handle_offsets.json` entirely once spec replay matches the old
  replay on every program in the ladder.

### Phase 4 — fd / size / output-region classification
- Extend inference with remaining field kinds.
- Needed before codegen is meaningful — a stub that doesn't know which
  bytes are pointers can't flatten.
- Switch the optimizer to multi-objective / Pareto tracking here. The
  metrics are now naturally in tension: handle recall, pointer precision,
  size-field coverage, replay success, and false positive rate.

### Phase 5 — Codegen
- Implement `codegen/` templates. Target: regenerate enough of the
  aegis stub/daemon to handle the cuInit ioctl sequence automatically.
- Diff against the handwritten aegis code. Anything that differs is
  either a codegen gap or a spec gap — again, investigate both.
- Add generated-code compile errors and stub/daemon behavioral diffs to
  the optimizer's ASI. Only then should GEPA be allowed to propose
  template changes.

### Phase 6 — Second accelerator
- Capture an AMD ROCm program (or Intel Gaudi). Run inference.
- The quality of the spec on a driver we've never touched is the real
  test of generalisability. If it works, the trace-driven thesis holds.
- Treat this as the first **generalization** evaluation: train/optimize
  on NVIDIA traces, validate on the second accelerator without changing
  core sniffer, inference, or replay logic.

### Phase 7 — Policy mediator
- Only after codegen stabilises. Implements the security contribution
  from newdirection.md.

---

## Success Criteria

The generalisation is successful when:

1. Adding support for a new CUDA application requires **zero** hand-edits
   to any schema or offset table — you capture, infer, replay.
2. A newly-released driver version (say NVIDIA 580.x) can be supported
   by re-running inference on a fresh capture, with the spec diff
   showing only the genuinely new ioctls.
3. The aegis stub + daemon for a given driver are **generated**, not
   written. The repo of handwritten C shrinks, not grows, as coverage
   expands.
4. A second accelerator (ROCm / Gaudi / TPU) gets non-trivial replay
   working without modifying anything under `sniffer/`, `infer/`, or
   `replay/` — only new test programs and a new `spec/*.spec.json`.

---

## Open Questions

1. **How many runs does inference need?** Handle detection works with 2.
   Pointer detection probably works with 1 (just check against maps).
   Size/length correlation might need many invocations of the same
   ioctl — does the cuInit trace have enough?
2. **Where do semantic names come from?** Inference can tell you a field
   is a handle; it can't tell you it's `hClient`. Some human-authored
   name overlay is probably still needed — but as a thin annotation
   layer, not a source of truth.
3. **How do we detect nested structures (pointers to structs with more
   pointers)?** A pointer field whose target buffer also contains
   pointers needs recursive classification. Probably phase 4+.
4. **Does the spec format need to express sharing policies** (AvA-style
   — "this buffer is caller-owned, this one is shared")? Likely yes
   for codegen, but can be deferred until phase 5 forces the question.
