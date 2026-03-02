# Plan v4: From Replay to Programmable Kernel Dispatch

## Goal

Dispatch a **new, user-provided CUDA kernel** (PTX) to the GPU using raw ioctls — without calling any `libcuda.so` function after initialization.

---

## What We Have (v3 Replay System)

| Component | Status | What it does |
|---|---|---|
| `intercept/libnv_sniff.so` | ✅ Working | LD_PRELOAD sniffer — captures open/ioctl/close to JSONL |
| `replay/handle_map.py` | ✅ Working | Remaps file descriptors and RM handles between sessions |
| `replay/replay.py` | ✅ Working | Reads JSONL, re-opens devices, re-issues ioctls byte-for-byte |
| `intercept/handle_offsets.json` | ✅ Working | Schema: which byte offsets in each ioctl hold handles/fds |
| `tools/find_handle_offsets.py` | ✅ Working | Auto-discovers handle offsets by diffing two captures |
| 10 test programs + captures | ✅ Working | cu_init through matmul, all replay at 0 failures |

**What the replay system proves:** We fully understand the session layer — device opens, RM handle chains, fd embedding. We can "talk to" the NVIDIA kernel driver without `libcuda.so` at replay time.

**What the replay system cannot do:** It replays a fixed ioctl tape. It cannot substitute a different kernel, change grid dimensions, or pass different arguments. It's a VCR, not a programmable interface.

---

## What We Need (Programmable Dispatch)

To dispatch a *new* kernel, we need to understand and control these **five ioctl-level operations**:

| Operation | CUDA API equivalent | What happens at ioctl level | Status |
|---|---|---|---|
| 1. **Session setup** | `cuInit`, `cuDeviceGet`, `cuCtxCreate` | ~575 ioctls to open devices, create RM objects, register UVM | ✅ Can replay from template |
| 2. **Module load** | `cuModuleLoadData(ptx)` | PTX string lives in user-space memory; an ioctl passes a *pointer* to it; driver JIT-compiles to SASS | ❌ Don't know which ioctl carries the PTX pointer or where in the buffer it sits |
| 3. **Function lookup** | `cuModuleGetFunction(&fn, mod, "name")` | ioctl queries function handle by name string (also a pointer) | ❌ Don't know the struct format |
| 4. **Memory + launch** | `cuMemAlloc`, `cuMemcpyHtoD`, `cuLaunchKernel` | Alloc GPU memory, DMA host→device, submit launch descriptor with grid dims + kernel arg pointers | ❌ Know handle offsets but not semantic field layout |
| 5. **Teardown** | `cuCtxDestroy` | ~200 ioctls to destroy handles, close fds | ✅ Can replay from template |

### The Core Gap

The sniffer captures the ioctl buffer (up to 4096 bytes) but **not the data pointed to by pointers within those buffers**. When `libcuda.so` calls `ioctl(fd, REQ, &params)` for module load, `params` contains a **user-space pointer** to the PTX string. The kernel driver dereferences that pointer to read the PTX. Our capture only has the pointer value (an address like `0x7f3a...`), not the PTX bytes themselves.

This means:
- Even if we replay the module-load ioctl, the pointer in the buffer points to memory that **doesn't exist** in the replay process (or contains garbage)
- Yet replay succeeds at 0 failures — this tells us the driver may cache/reuse previously-JIT'd code, or the pointer is only used once and subsequent references go through the module handle
- To load a *different* kernel, we must: (a) put our new PTX in memory, (b) patch the pointer in the ioctl buffer, (c) possibly adjust size fields

---

## Comparison: What Changes Between Kernels?

Data from diffing `vector_add.jsonl` vs `matmul.jsonl` (both have exactly 781 ioctls):

| Category | Req codes | Buffer size | Bytes that differ | Interpretation |
|---|---|---|---|---|
| **RM control** (`nvidiactl`) | `0xC020462A` (296), `0xC030462B` (124), `0xC0104629` (111), etc. | 16–56 bytes | 1–6 bytes each | Handle values (already patched by replay) |
| **UVM management** (`nvidia-uvm`) | `0x00000022` (27), `0x0000001B` (20), `0x0000001C` (20), etc. | 4096 bytes | 800–2000 bytes each | GPU virtual addresses, page table entries, channel objects — kernel-dependent |
| **UVM init** (`nvidia-uvm`) | `0x30000001`, `0x0000004B`, `0x00000027` | 4096 bytes | ~1256 bytes each | Process/channel registration — has PID, timestamps, addresses |

**Key finding:** The `nvidiactl` ioctls (RM layer) change very little between kernels — just handle values we already know how to patch. The `nvidia-uvm` ioctls (memory management layer) change significantly because different kernels get different GPU virtual addresses, page table mappings, and memory layouts.

---

## Strategy Options

### Option A: "Enhanced Sniffer" (Recommended — most tractable)

**Idea:** Enhance the sniffer to capture pointer-referenced data (PTX strings, kernel arg buffers), then build a "template + patch" system.

**Pros:** Builds directly on what we have. Doesn't require full ioctl reverse-engineering.
**Cons:** Still template-based — only works for kernels with the same memory footprint as the template.

### Option B: "Full ioctl Reverse-Engineering"

**Idea:** Reverse-engineer the exact struct layout of every ioctl (all 31 req codes). Build a Python library that constructs ioctls from scratch.

**Pros:** Complete control. Can dispatch any kernel with any parameters.
**Cons:** Extremely labor-intensive. NVIDIA changes ioctl formats between driver versions. Requires understanding closed-source struct definitions.

### Option C: "Hybrid" (Practical shortcut)

**Idea:** Use `libcuda.so` for session setup + module load + function lookup (the hard parts), then intercept the launch ioctl and learn to construct it from scratch.

**Pros:** Get a working dispatch quickly. Focus reverse-engineering on the launch path only.
**Cons:** Still depends on `libcuda.so` for module loading. Doesn't fully eliminate the dependency.

**Recommended path: Option A first, then graduate to Option B for the launch path.**

---

## Execution Plan (Option A)

### Phase 0 — Enhanced Sniffer: Capture Pointer-Referenced Data

**Goal:** Modify `nv_sniff.c` to follow pointers embedded in ioctl buffers and capture the data they point to.

**What we know:**
- NVIDIA RM ioctls on `nvidiactl` use a fixed format: the ioctl buffer is a small struct (16–56 bytes) that contains handles and sometimes a pointer to a larger "params" struct in user-space
- The `0xC038464E` ioctl (RM_ALLOC) is 56 bytes and creates RM objects — the module object is created here, and its params struct likely contains the PTX pointer
- UVM ioctls on `nvidia-uvm` are always 4096 bytes and are self-contained (no external pointers)

**Actions:**
1. Study the open-source NVIDIA kernel driver headers (`nv-ioctl.h`, `nv_uvm_ioctl.h`) to identify the RM ioctl struct format
2. For RM ioctls that contain a `params_ptr` field, dereference the pointer and capture the pointed-to data as a `"params"` field in the JSONL
3. Specifically target the ioctl that carries the PTX data for `cuModuleLoadData`

**Deliverable:** Enhanced sniffer that captures both the ioctl buffer AND any user-space data referenced by pointers within it.

### Phase 1 — Identify the Module-Load Ioctl

**Goal:** Determine exactly which ioctl (req code + seq range) is responsible for passing PTX to the driver for JIT compilation.

**Actions:**
1. Capture `cu_module_load` with the enhanced sniffer
2. Search the captured params data for the PTX string (`.target sm_75`, `null_kernel`, etc.)
3. Record: which req code, which byte offset in the params struct holds the PTX pointer, which field holds the PTX length
4. Verify by capturing two programs with different PTX and confirming the PTX bytes differ at the identified location

**Deliverable:** Documentation of the module-load ioctl format: `{ req, ptx_pointer_offset, ptx_length_offset, ... }`.

### Phase 2 — Identify the Function-Lookup and Launch Ioctls

**Goal:** Determine which ioctls correspond to `cuModuleGetFunction` and `cuLaunchKernel`.

**Actions:**
1. Compare `cu_module_load` (no launch) vs `cu_launch_null` (with launch) to find the extra ioctls
2. The function-lookup ioctl passes a function name string by pointer — find it in the enhanced capture
3. The launch ioctl passes grid dimensions, block dimensions, and kernel argument pointers — identify the struct layout by varying these parameters and observing which bytes change

**Deliverable:** Documentation of the launch descriptor ioctl format: `{ req, function_handle_offset, grid_dims_offset, block_dims_offset, args_pointer_offset, ... }`.

### Phase 3 — Build the Template Splicing Engine

**Goal:** Split a captured trace into reusable segments and parameterize the kernel-specific parts.

**Architecture:**
```
template_dispatch.py
├── load_template(jsonl_path) → TemplateTrace
│     — parses JSONL into segments: SETUP, MODULE_LOAD, FUNCTION_LOOKUP, LAUNCH, TEARDOWN
│
├── class TemplateTrace
│     ├── setup_events: list[dict]       — context creation (replayable as-is)
│     ├── module_load_events: list[dict]  — needs PTX pointer patching
│     ├── launch_events: list[dict]       — needs grid/block/args patching
│     ├── teardown_events: list[dict]     — replayable as-is
│     └── dispatch(ptx: str, func_name: str, grid, block, args) → int
│           — replays setup, patches module_load with new PTX, patches launch, replays teardown
│
└── main()
      — CLI: template_dispatch.py --template matmul.jsonl --ptx new_kernel.ptx --func "my_kernel" --grid 1,1,1 --block 128,1,1
```

**Deliverable:** A Python tool that takes a captured trace + new PTX + launch parameters and dispatches the new kernel to the GPU.

### Phase 4 — Validate: Dispatch a Modified Kernel

**Goal:** Prove the system works by dispatching a slightly different matmul kernel (e.g., 64×64 instead of 128×128) using the 128×128 template.

**Actions:**
1. Write a new PTX kernel `matmul_64.ptx` for 64×64 matrices
2. Use the matmul template trace
3. Call `template_dispatch.py` with the new PTX and adjusted grid dimensions
4. Read back results via the memcpy ioctls
5. Verify correctness

**Deliverable:** A working end-to-end demonstration of kernel dispatch without `libcuda.so`.

### Phase 5 — Memory Management (Stretch Goal)

**Goal:** Understand the memory allocation ioctls well enough to allocate different-sized buffers.

The template approach breaks if the new kernel needs more (or differently-shaped) GPU memory than the template. This phase would reverse-engineer the memory allocation ioctls to allow arbitrary `cuMemAlloc`-equivalent calls.

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| PTX pointer is in a nested struct (pointer → pointer → PTX) | Medium | High — hard to follow chains | Study open-source nvidia-open headers for struct definitions |
| Driver caches JIT results and ignores PTX on replay | Medium | High — explains why replay works but substitution won't | Test by clearing driver caches between runs |
| ioctl format changes between driver versions | Certain (over time) | Medium | Pin to current driver version; document format per version |
| UVM ioctls contain GPU virtual addresses that can't be reused | High | Medium | Must let the driver assign new addresses and patch forward |
| Launch descriptor format is complex (embedded sub-structs) | High | Medium | Start with null kernel (no args), then add complexity |

---

## Success Criteria

| Milestone | What it proves |
|---|---|
| Enhanced sniffer captures PTX text | We can see what `libcuda.so` passes to the driver |
| Module-load ioctl format is documented | We understand how kernels enter the driver |
| Modified kernel dispatches successfully | We can program the GPU without `libcuda.so` |
| Different grid dimensions work | We understand the launch descriptor format |
| Different memory sizes work | We understand the memory management protocol |

---

## Files to Create/Modify

| File | Action | Purpose |
|---|---|---|
| `intercept/nv_sniff.c` | **Modify** | Add pointer-following capture for RM ioctl params |
| `intercept/nv_ioctl_structs.h` | **Create** | Struct definitions derived from nvidia-open headers |
| `tools/diff_captures.py` | **Create** | Diff two captures semantically (not just byte-level) |
| `tools/annotate_trace.py` | **Create** | Label each ioctl with its CUDA API equivalent |
| `replay/template_dispatch.py` | **Create** | Template-based kernel dispatch engine |
| `programs/matmul_64.cu` | **Create** | Validation program: 64×64 matmul with different PTX |
