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
   │                      harness / agent                         │
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
   └──────────────────────────────────────────────────────────────┘
```

### The four drivers inside the harness

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
├── codegen/                 # new — emits aegis stub + daemon
│   ├── stub.c.jinja
│   └── daemon.c.jinja
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

### Phase 3 — Generic replay
- Rewrite `replay.py` as a spec interpreter.
- Drop `handle_offsets.json` entirely once spec replay matches the old
  replay on every program in the ladder.

### Phase 4 — fd / size / output-region classification
- Extend inference with remaining field kinds.
- Needed before codegen is meaningful — a stub that doesn't know which
  bytes are pointers can't flatten.

### Phase 5 — Codegen
- Implement `codegen/` templates. Target: regenerate enough of the
  aegis stub/daemon to handle the cuInit ioctl sequence automatically.
- Diff against the handwritten aegis code. Anything that differs is
  either a codegen gap or a spec gap — again, investigate both.

### Phase 6 — Second accelerator
- Capture an AMD ROCm program (or Intel Gaudi). Run inference.
- The quality of the spec on a driver we've never touched is the real
  test of generalisability. If it works, the trace-driven thesis holds.

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

   Here is the revised `roadmap.md`, updated to incorporate the control/data plane split, the `SIGSEGV` doorbell trap, fuzzer containment, and the strict dichotomy between the offline harness (where accuracy is everything) and the emitted runtime (where latency is everything).

***

# Roadmap — Generalising `ioctl-cuda-mapping` into a Trace-Driven Virtualization Toolkit

## Date: 2026-04-15

## Purpose

This document describes the target architecture for turning the existing
`ioctl-cuda-mapping` repo into a **device-agnostic, trace-driven toolkit** that produces machine-readable specifications of driver protocols (both control-plane `ioctl`s and data-plane memory maps). 

It consumes those specifications to drive replay, virtualization stubs, and policy mediation.

---

## The Main Idea: The Offline/Online Dichotomy

We are **not building a GPU virtualization layer**. We are building an **offline harness** that produces virtualization layers from traces. 

This architecture demands a strict separation of concerns:
1.  **The Offline Harness (The Generator):** Performance and latency do not matter here. A trace execution can take 1,000x longer than native execution. The sole objective is **100% protocol accuracy** and perfect state capture.
2.  **The Emitted Runtime (The Product):** The generated guest stubs, host daemon, and policy mediator. Here, **latency is existential**. The daemon must not rely on slow traps (like `SIGSEGV`) for steady-state data-plane execution. It relies entirely on the precise memory layouts and semantics defined in the `spec.json` to achieve bare-metal-adjacent latency.

The spec file is the fixed point. A new driver version is a re-run of the harness against a fresh corpus. A new accelerator (AMD, Intel, TPU) is a new corpus.

### The Harness Loop (Updated)

```text
   ┌──────────────────────────────────────────────────────────────┐
   │                    OFFLINE HARNESS                           │
   │                                                              │
   │  pick a program  ──►  run inside VFIO VM ──► sniff ioctl &   │
   │         ▲             (Blast Containment)    doorbell traps  │
   │         │                                          │         │
   │   mutate inputs                                    ▼         │
   │   / pick next                            inference engine    │
   │   program                                (Shift-and-Test)    │
   │         ▲                                          │         │
   │         │                                      spec delta    │
   │         │                                          │         │
   │  validate: replay                                  ▼         │
   │  trace via generic                       update spec.json    │
   │  interpreter     ◄─────────────────────────────────┘         │
   │         │                                                    │
   │         ▼                                                    │
   │  disagreements / low-confidence fields                       │
   │         │                                                    │
   │         ▼                                                    │
   │  escalate: ask LLM / human prior                             │
   └──────────────────────────────────────────────────────────────┘
```

---

## Component Breakdown & Technical Strategy

### 1. Generic Sniffer (`intercept/`) — The Control & Data Plane Trap

**Target:** Capture both synchronous setup (`ioctl`) and asynchronous command submissions (`mmap`).

* **Control Plane (`ioctl`):** Intercept via `LD_PRELOAD` or kernel-space hooks. Device filter becomes a runtime-configurable glob list (`NV_SNIFF_DEVICES=/dev/nvidia*:/dev/kfd`).
* **Data Plane (The Doorbell Trap):** Modern drivers bypass the kernel for execution by writing to memory-mapped ring buffers. The sniffer must capture these.
    * Hook `mmap` calls. Allocate a userspace shadow buffer.
    * Use `mprotect(PROT_READ)` to remove write permissions from the true GPU queue.
    * When the userspace driver submits a command, it triggers a `SIGSEGV`.
    * The signal handler catches the fault, decodes the instruction pointer to log the exact address and byte payload (the "Data Plane Trace Event"), unprotects the page to let the write succeed, and reprotects it.
    * *Note: Recent literature (e.g., KRYPTON, USENIX ATC '25) validates `mprotect` for kernel-space GPU access control. We repurpose it here purely for offline tracing.*

### 2. Schema Inference Engine (`infer/`) & Ephemeral Fuzzer

Given N independent runs, classify each byte range.

* **Blast-Radius Containment:** The mutation driver *will* cause kernel panics when fuzzing sizes and grid dimensions. The mutation loop runs inside an **ephemeral VFIO VM** with PCIe passthrough. On kernel panic, the host drops the VM, reverts to a microsecond-old snapshot, and continues.
* **Pointer Validation (Shift-and-Test):** To avoid false positives where compiled shader binaries look like valid `/proc/self/maps` addresses:
    * When inference suspects a pointer, the mutation engine allocates a new dummy buffer at a different address.
    * It overwrites the suspected pointer with the dummy address.
    * If the driver accepts the `ioctl` and writes output to the dummy buffer, it is a confirmed pointer. Otherwise, it is an inline constant/hash.

| Classification   | Detection heuristic                                             |
|------------------|-----------------------------------------------------------------|
| **handle** | Changes across runs; values cluster; used in later ioctls.      |
| **pointer** | Matches `maps`, verified via **Shift-and-Test** mutation.       |
| **fd** | Small int (< 1024) matching an open fd at capture time.         |
| **size/length** | Correlates with adjacent buffer length (requires fuzzing).      |
| **inline-value** | Stable across runs, or varies without above signals.            |
| **output-region**| Bytes where `after` differs from `before` in the same call.     |

Output: `spec/<driver>.spec.json` containing highly-confident, field-by-field schemas.

### 3. Stub/Daemon Codegen (`codegen/`) — The Latency-Critical Runtime

The JSON spec emits the C/Rust code for the production virtualization layer (guest stub + host daemon).

* **No runtime introspection:** The generated code does not guess. It flattens structs, translates handles, and copies sizes exactly as dictated by the spec.
* **Data Plane Passthrough:** Because the offline harness accurately mapped the memory layout, the emitted host daemon can safely map the user-mode queues directly into the guest VM. The host daemon only mediates the control plane (`ioctl`), keeping steady-state execution latency virtually identical to bare metal.

### 4. Policy Mediator (`mediator/`) 

Sits in the host daemon. Enforces per-tenant SLAs, memory limits, and rate limits. Relies on the spec's `size` and `pointer` classifications to securely audit payloads before forwarding them to the physical driver.

---

## Migration Plan

### Phase 0 — Freeze the Baseline
* Document existing JSONL schema. Tag current repo as `v0-nvidia-only`.

### Phase 1 — Generalise Control Plane & Add Data Plane Trap
* Replace `/dev/nvidia*` filter with a glob list. Add `path`, `maps`, and `fd_table` tracking.
* Implement the `mprotect`/`SIGSEGV` doorbell trap for memory-mapped queues.
* Verify existing CUDA ladder replays byte-for-byte. 

### Phase 2 — Fuzzer Containment & Inference v1
* Set up KVM/QEMU VFIO automated snapshot/revert infrastructure.
* Port `find_handle_offsets.py` to `infer/classify.py`.
* Implement `Shift-and-Test` validation for pointer detection.
* **Acceptance:** Emitted `spec.json` matches handwritten legacy tables with >95% agreement.

### Phase 3 — Generic Replay
* Rewrite `replay.py` as a dumb spec interpreter. Drop handwritten offset tables entirely.

### Phase 4 — Codegen & Latency Auditing
* Implement Jinja templates for guest stub / host daemon.
* Target: Regenerate enough code to handle the `cuInit` sequence and a basic matrix multiplication.
* **Acceptance:** Benchmark the generated daemon against bare-metal. Control-plane overhead must be bounded; data-plane overhead must be zero (via proper shared-memory mapping derived from the spec).

### Phase 5 — The Second Accelerator (ROCm)
* Capture an AMD ROCm program. Run inference. 
* If the system can generate a working ROCm virtualization stub without manual C code, the research thesis is proven.

### Phase 6 — Policy Mediator
* Implement memory and throughput limiters utilizing the parsed struct sizes.
