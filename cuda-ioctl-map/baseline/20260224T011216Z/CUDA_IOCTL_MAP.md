# CUDA → ioctl Mapping Report

> **Environment:** Linux, CUDA 12.5 Driver API, strace-based
> **Method:** Cumulative programs. Per-step metrics:
> - *Code-set delta* — request codes not seen in any previous step
> - *Event delta* — per-code frequency changes vs previous step
> - *Confidence* — H=high / M=medium / L=low⚠ / N=none⚠ (low+none flagged for review)
> - *Repro* — ✓ deterministic across runs / ⚠ R/N inconsistent / ? not checked

---

## `cu_init`

| Property | Value |
|----------|-------|
| Devices touched | `/dev/nvidia-uvm, /dev/nvidia0, /dev/nvidia1, /dev/nvidia2, /dev/nvidiactl` |
| Total ioctls (cumulative) | 230 |
| Unique ioctl codes | 15 |
| **New codes vs prev** | **15** |
| **Net new events vs prev** | **230** |
| Reproducibility | ✓ (3 runs) |

#### Confidence summary (unique codes)
| High | Medium | Low ⚠ | None ⚠ | Total flagged for review |
|------|--------|--------|--------|--------------------------|
| 7 | 6 | 2 | 0 | 2 |

### New ioctls introduced (code-set delta)

| # | Device | Request Code | Name | Description | Phase | Conf | Repro |
|---|--------|-------------|------|-------------|-------|------|-------|
| 1 | `/dev/nvidiactl` | `0xC00846D6` | NV_ESC_CARD_INFO | Query basic GPU card information / card presence check | device query | medium | ✓ |
| 2 | `/dev/nvidiactl` | `0xC90046C8` | NV_ESC_ATTACH_GPUS_TO_FD | Attach GPU devices to a file descriptor for subsequent RM calls | initialization | high | ✓ |
| 3 | `/dev/nvidiactl` | `0xC020462B` | NV_ESC_RM_ALLOC_MEMORY | Allocate a memory object (device or system memory) via RM | memory allocation | high | ✓ |
| 4 | `/dev/nvidiactl` | `0xC020462A` | NV_ESC_RM_ALLOC | Allocate an RM object (root client, device, subdevice, channel, context dma) | object allocation | high | ✓ |
| 5 | `/dev/nvidiactl` | `0xC0104629` | NV_ESC_RM_CONTROL | Execute an RM control command on a GPU object (query/set properties) | object control | high | ✓ |
| 6 | `/dev/nvidiactl` | `0xC030462B` | NV_ESC_RM_ALLOC (large) | Allocate RM object with larger parameter struct | object allocation | medium | ✓ |
| 7 | `/dev/nvidia-uvm` | `0x30000001` | NV_UVM_INITIALIZE | Initialize the NVIDIA UVM (Unified Virtual Memory) driver | initialization | high | ✓ |
| 8 | `/dev/nvidia-uvm` | `0x0000004B` | NV_ESC_RM_CONTROL (simple) ⚠ | RM control call without size encoding (older ioctl form) | object control | low | ✓ |
| 9 | `/dev/nvidia-uvm` | `0x00000027` | UVM_REGISTER_GPU_VASPACE | Register GPU virtual address space with UVM | initialization | medium | ✓ |
| 10 | `/dev/nvidiactl` | `0xC00C46D1` | NV_ESC_GPU_ENUM_BOUNDARY ⚠ | Fired by libcuda on /dev/nvidiactl immediately after a sequential openat() probe of /dev/nvidiaX fails (EIO or ENOENT). Part of the GPU enumeration loop: libcuda probes /dev/nvidia0, /dev/nvidia1 ... /dev/nvidiaN in order until one fails, then calls this ioctl to notify the RM of the device boundary. Present on any machine where the number of physical GPUs is less than the highest probed index. Confirmed normal behavior: fires on every cuInit run on a 3-GPU system (probe of /dev/nvidia3 fails → this ioctl fires → enumeration ends). Absent on systems with 4 GPUs because /dev/nvidia3 opens successfully. | device enumeration | low | ✓ |
| 11 | `/dev/nvidia0` | `0xC00446C9` | NV_ESC_REGISTER_FD | Register a file descriptor with the NVIDIA RM for GPU access | initialization | high | ✓ |
| 12 | `/dev/nvidia0` | `0xC23046D7` | NV_ESC_NUMA_INFO | Query NUMA topology information for GPU memory | device query / initialization | medium | ✓ |
| 13 | `/dev/nvidiactl` | `0xC038464E` | NV_ESC_RM_VID_HEAP_CONTROL | Video heap (framebuffer/BAR1) memory management control | memory management | high | ✓ |
| 14 | `/dev/nvidia-uvm` | `0x00000025` | UVM_REGISTER_GPU | Register a GPU with the UVM driver for unified memory management | initialization | medium | ✓ |
| 15 | `/dev/nvidia-uvm` | `0x00000017` | UVM_MAP_EXTERNAL_ALLOCATION | Map an external (non-UVM managed) allocation into the UVM address space | memory mapping | medium | ✓ |

### Event-level changes vs prev (frequency delta)

| Request Code | Name | Prev count | Cur count | Delta |
|-------------|------|-----------|----------|-------|
| `0x00000017` | UVM_MAP_EXTERNAL_ALLOCATION | 0 | 1 | ▲1 |
| `0x00000025` | UVM_REGISTER_GPU | 0 | 3 | ▲3 |
| `0x00000027` | UVM_REGISTER_GPU_VASPACE | 0 | 1 | ▲1 |
| `0x0000004B` | NV_ESC_RM_CONTROL (simple) | 0 | 1 | ▲1 |
| `0x30000001` | NV_UVM_INITIALIZE | 0 | 1 | ▲1 |
| `0xC00446C9` | NV_ESC_REGISTER_FD | 0 | 6 | ▲6 |
| `0xC00846D6` | NV_ESC_CARD_INFO | 0 | 2 | ▲2 |
| `0xC00C46D1` | NV_ESC_GPU_ENUM_BOUNDARY | 0 | 1 | ▲1 |
| `0xC0104629` | NV_ESC_RM_CONTROL | 0 | 4 | ▲4 |
| `0xC020462A` | NV_ESC_RM_ALLOC | 0 | 178 | ▲178 |
| `0xC020462B` | NV_ESC_RM_ALLOC_MEMORY | 0 | 2 | ▲2 |
| `0xC030462B` | NV_ESC_RM_ALLOC (large) | 0 | 22 | ▲22 |
| `0xC038464E` | NV_ESC_RM_VID_HEAP_CONTROL | 0 | 3 | ▲3 |
| `0xC23046D7` | NV_ESC_NUMA_INFO | 0 | 3 | ▲3 |
| `0xC90046C8` | NV_ESC_ATTACH_GPUS_TO_FD | 0 | 2 | ▲2 |

---

## `cu_device_get`

| Property | Value |
|----------|-------|
| Devices touched | `/dev/nvidia-uvm, /dev/nvidia0, /dev/nvidia1, /dev/nvidia2, /dev/nvidiactl` |
| Total ioctls (cumulative) | 230 |
| Unique ioctl codes | 15 |
| **New codes vs prev** | **0** |
| **Net new events vs prev** | **0** |
| Reproducibility | ✓ (3 runs) |

#### Confidence summary (unique codes)
| High | Medium | Low ⚠ | None ⚠ | Total flagged for review |
|------|--------|--------|--------|--------------------------|
| 7 | 6 | 2 | 0 | 2 |

*No new ioctl codes introduced by this call.*

*No event-frequency changes vs previous step.*

---

## `cu_ctx_create`

| Property | Value |
|----------|-------|
| Devices touched | `/dev/nvidia-uvm, /dev/nvidia0, /dev/nvidia1, /dev/nvidia2, /dev/nvidiactl` |
| Total ioctls (cumulative) | 575 |
| Unique ioctl codes | 25 |
| **New codes vs prev** | **10** |
| **Net new events vs prev** | **345** |
| Reproducibility | ✓ (5 runs) |

#### Confidence summary (unique codes)
| High | Medium | Low ⚠ | None ⚠ | Total flagged for review |
|------|--------|--------|--------|--------------------------|
| 8 | 10 | 7 | 0 | 7 |

### New ioctls introduced (code-set delta)

| # | Device | Request Code | Name | Description | Phase | Conf | Repro |
|---|--------|-------------|------|-------------|-------|------|-------|
| 1 | `/dev/nvidia-uvm` | `0x00000019` | UVM_REGISTER_CHANNEL | Register a GPU channel with UVM for memory access tracking | context setup | medium | ✓ |
| 2 | `/dev/nvidia-uvm` | `0x00000049` | UVM_MAP_EXTERNAL_SPARSE ⚠ | Map sparse external memory into UVM range | memory mapping | low | ✓ |
| 3 | `/dev/nvidia-uvm` | `0x00000021` | UVM_ALLOC_SEMAPHORE_POOL ⚠ | Allocate a semaphore pool for GPU synchronization | context setup | low | ✓ |
| 4 | `/dev/nvidiactl` | `0xC028465E` | NV_ESC_RM_DUP_OBJECT | Duplicate an RM object handle across clients/contexts | context setup | medium | ✓ |
| 5 | `/dev/nvidia-uvm` | `0x0000001B` | UVM_MAP_DYNAMIC_PARALLELISM_REGION ⚠ | Map a dynamic parallelism region in UVM for child kernel launches | context setup | low | ✓ |
| 6 | `/dev/nvidia0` | `0xC01046CE` | NV_ESC_CHECK_VERSION_STR | Check driver version string compatibility between userspace and kernel | initialization | high | ✓ |
| 7 | `/dev/nvidia-uvm` | `0x00000044` | UVM_SET_PREFERRED_LOCATION ⚠ | Set preferred memory location hint for a UVM allocation | memory management | low | ✓ |
| 8 | `/dev/nvidia-uvm` | `0x00000048` | UVM_CREATE_EXTERNAL_RANGE ⚠ | Create an external memory range within the UVM address space | context setup | low | ✓ |
| 9 | `/dev/nvidia0` | `0xC0384627` | NV_ESC_RM_SHARE | Share an RM resource between GPU contexts | context setup | medium | ✓ |
| 10 | `/dev/nvidia-uvm` | `0x00000041` | UVM_ENABLE_PEER_ACCESS | Enable peer-to-peer memory access between GPUs via UVM | context setup | medium | ✓ |

### Event-level changes vs prev (frequency delta)

| Request Code | Name | Prev count | Cur count | Delta |
|-------------|------|-----------|----------|-------|
| `0x00000017` | UVM_MAP_EXTERNAL_ALLOCATION | 1 | 10 | ▲9 |
| `0x00000019` | UVM_REGISTER_CHANNEL | 0 | 1 | ▲1 |
| `0x0000001B` | UVM_MAP_DYNAMIC_PARALLELISM_REGION | 0 | 20 | ▲20 |
| `0x00000021` | UVM_ALLOC_SEMAPHORE_POOL | 0 | 24 | ▲24 |
| `0x00000041` | UVM_ENABLE_PEER_ACCESS | 0 | 1 | ▲1 |
| `0x00000044` | UVM_SET_PREFERRED_LOCATION | 0 | 1 | ▲1 |
| `0x00000048` | UVM_CREATE_EXTERNAL_RANGE | 0 | 1 | ▲1 |
| `0x00000049` | UVM_MAP_EXTERNAL_SPARSE | 0 | 24 | ▲24 |
| `0xC00446C9` | NV_ESC_REGISTER_FD | 6 | 14 | ▲8 |
| `0xC01046CE` | NV_ESC_CHECK_VERSION_STR | 0 | 8 | ▲8 |
| `0xC020462A` | NV_ESC_RM_ALLOC | 178 | 292 | ▲114 |
| `0xC028465E` | NV_ESC_RM_DUP_OBJECT | 0 | 1 | ▲1 |
| `0xC030462B` | NV_ESC_RM_ALLOC (large) | 22 | 123 | ▲101 |
| `0xC0384627` | NV_ESC_RM_SHARE | 0 | 5 | ▲5 |
| `0xC038464E` | NV_ESC_RM_VID_HEAP_CONTROL | 3 | 30 | ▲27 |

---

## `cu_ctx_destroy`

| Property | Value |
|----------|-------|
| Devices touched | `/dev/nvidia-uvm, /dev/nvidia0, /dev/nvidia1, /dev/nvidia2, /dev/nvidiactl` |
| Total ioctls (cumulative) | 776 |
| Unique ioctl codes | 31 |
| **New codes vs prev** | **6** |
| **Net new events vs prev** | **201** |
| Reproducibility | ✓ (5 runs) |

#### Confidence summary (unique codes)
| High | Medium | Low ⚠ | None ⚠ | Total flagged for review |
|------|--------|--------|--------|--------------------------|
| 9 | 13 | 9 | 0 | 9 |

### New ioctls introduced (code-set delta)

| # | Device | Request Code | Name | Description | Phase | Conf | Repro |
|---|--------|-------------|------|-------------|-------|------|-------|
| 1 | `/dev/nvidia-uvm` | `0x00000022` | UVM_PAGEABLE_MEM_ACCESS ⚠ | Query/configure pageable memory access support in UVM | context setup | low | ✓ |
| 2 | `/dev/nvidia0` | `0xC01046CF` | NV_ESC_CHECK_VERSION_STR (variant) | Driver version check (alternate size variant) | initialization | medium | ✓ |
| 3 | `/dev/nvidiactl` | `0xC020464F` | NV_ESC_RM_MAP_MEMORY | Map GPU memory into the process virtual address space | memory mapping | high | ✓ |
| 4 | `/dev/nvidia-uvm` | `0x00000018` | UVM_UNREGISTER_GPU | Unregister a GPU from UVM | teardown | medium | ✓ |
| 5 | `/dev/nvidia-uvm` | `0x0000001C` | UVM_UNMAP_EXTERNAL ⚠ | Unmap an external allocation from the UVM address space | teardown | low | ✓ |
| 6 | `/dev/nvidia-uvm` | `0x0000001A` | UVM_UNREGISTER_CHANNEL | Unregister a GPU channel from UVM | context teardown | medium | ✓ |

### Event-level changes vs prev (frequency delta)

| Request Code | Name | Prev count | Cur count | Delta |
|-------------|------|-----------|----------|-------|
| `0x00000018` | UVM_UNREGISTER_GPU | 0 | 9 | ▲9 |
| `0x0000001A` | UVM_UNREGISTER_CHANNEL | 0 | 1 | ▲1 |
| `0x0000001C` | UVM_UNMAP_EXTERNAL | 0 | 20 | ▲20 |
| `0x00000022` | UVM_PAGEABLE_MEM_ACCESS | 0 | 26 | ▲26 |
| `0xC0104629` | NV_ESC_RM_CONTROL | 4 | 110 | ▲106 |
| `0xC01046CF` | NV_ESC_CHECK_VERSION_STR (variant) | 0 | 8 | ▲8 |
| `0xC020462A` | NV_ESC_RM_ALLOC | 292 | 296 | ▲4 |
| `0xC020464F` | NV_ESC_RM_MAP_MEMORY | 0 | 27 | ▲27 |

---
