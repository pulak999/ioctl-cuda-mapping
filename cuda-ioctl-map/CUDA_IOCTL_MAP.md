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
| Devices touched | `/dev/nvidia-uvm, /dev/nvidia0, /dev/nvidia1, /dev/nvidia2, /dev/nvidia3, /dev/nvidiactl` |
| Total ioctls (cumulative) | 333 |
| Unique ioctl codes | 16 |
| **New codes vs prev** | **16** |
| **Net new events vs prev** | **333** |
| Reproducibility | ✓ (3 runs) |

#### Confidence summary (unique codes)
| High | Medium | Low ⚠ | None ⚠ | Total flagged for review |
|------|--------|--------|--------|--------------------------|
| 8 | 7 | 1 | 0 | 1 |

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
| 10 | `/dev/nvidia0` | `0xC00446C9` | NV_ESC_REGISTER_FD | Register a file descriptor with the NVIDIA RM for GPU access | initialization | high | ✓ |
| 11 | `/dev/nvidia0` | `0xC23046D7` | NV_ESC_NUMA_INFO | Query NUMA topology information for GPU memory | device query / initialization | medium | ✓ |
| 12 | `/dev/nvidia0` | `0xC01046CE` | NV_ESC_CHECK_VERSION_STR | Check driver version string compatibility between userspace and kernel | initialization | high | ✓ |
| 13 | `/dev/nvidiactl` | `0xC038464E` | NV_ESC_RM_VID_HEAP_CONTROL | Video heap (framebuffer/BAR1) memory management control | memory management | high | ✓ |
| 14 | `/dev/nvidia-uvm` | `0x00000025` | UVM_REGISTER_GPU | Register a GPU with the UVM driver for unified memory management | initialization | medium | ✓ |
| 15 | `/dev/nvidia-uvm` | `0x00000046` | NV_ESC_CARD_INFO (simple) | Query GPU card info (simple form without size encoding) | device query | medium | ✓ |
| 16 | `/dev/nvidia-uvm` | `0x00000017` | UVM_MAP_EXTERNAL_ALLOCATION | Map an external (non-UVM managed) allocation into the UVM address space | memory mapping | medium | ✓ |

### Event-level changes vs prev (frequency delta)

| Request Code | Name | Prev count | Cur count | Delta |
|-------------|------|-----------|----------|-------|
| `0x00000017` | UVM_MAP_EXTERNAL_ALLOCATION | 0 | 1 | ▲1 |
| `0x00000025` | UVM_REGISTER_GPU | 0 | 4 | ▲4 |
| `0x00000027` | UVM_REGISTER_GPU_VASPACE | 0 | 1 | ▲1 |
| `0x00000046` | NV_ESC_CARD_INFO (simple) | 0 | 4 | ▲4 |
| `0x0000004B` | NV_ESC_RM_CONTROL (simple) | 0 | 1 | ▲1 |
| `0x30000001` | NV_UVM_INITIALIZE | 0 | 1 | ▲1 |
| `0xC00446C9` | NV_ESC_REGISTER_FD | 0 | 12 | ▲12 |
| `0xC00846D6` | NV_ESC_CARD_INFO | 0 | 2 | ▲2 |
| `0xC0104629` | NV_ESC_RM_CONTROL | 0 | 5 | ▲5 |
| `0xC01046CE` | NV_ESC_CHECK_VERSION_STR | 0 | 4 | ▲4 |
| `0xC020462A` | NV_ESC_RM_ALLOC | 0 | 249 | ▲249 |
| `0xC020462B` | NV_ESC_RM_ALLOC_MEMORY | 0 | 2 | ▲2 |
| `0xC030462B` | NV_ESC_RM_ALLOC (large) | 0 | 37 | ▲37 |
| `0xC038464E` | NV_ESC_RM_VID_HEAP_CONTROL | 0 | 4 | ▲4 |
| `0xC23046D7` | NV_ESC_NUMA_INFO | 0 | 4 | ▲4 |
| `0xC90046C8` | NV_ESC_ATTACH_GPUS_TO_FD | 0 | 2 | ▲2 |

---

## `cu_device_get`

| Property | Value |
|----------|-------|
| Devices touched | `/dev/nvidia-uvm, /dev/nvidia0, /dev/nvidia1, /dev/nvidia2, /dev/nvidia3, /dev/nvidiactl` |
| Total ioctls (cumulative) | 333 |
| Unique ioctl codes | 16 |
| **New codes vs prev** | **0** |
| **Net new events vs prev** | **0** |
| Reproducibility | ✓ (3 runs) |

#### Confidence summary (unique codes)
| High | Medium | Low ⚠ | None ⚠ | Total flagged for review |
|------|--------|--------|--------|--------------------------|
| 8 | 7 | 1 | 0 | 1 |

*No new ioctl codes introduced by this call.*

*No event-frequency changes vs previous step.*

---

## `cu_ctx_create`

| Property | Value |
|----------|-------|
| Devices touched | `/dev/nvidia-uvm, /dev/nvidia0, /dev/nvidia1, /dev/nvidia2, /dev/nvidia3, /dev/nvidiactl` |
| Total ioctls (cumulative) | 814 |
| Unique ioctl codes | 31 |
| **New codes vs prev** | **15** |
| **Net new events vs prev** | **481** |
| Reproducibility | ✓ (5 runs) |

#### Confidence summary (unique codes)
| High | Medium | Low ⚠ | None ⚠ | Total flagged for review |
|------|--------|--------|--------|--------------------------|
| 9 | 14 | 8 | 0 | 8 |

### New ioctls introduced (code-set delta)

| # | Device | Request Code | Name | Description | Phase | Conf | Repro |
|---|--------|-------------|------|-------------|-------|------|-------|
| 1 | `/dev/nvidia-uvm` | `0x00000019` | UVM_REGISTER_CHANNEL | Register a GPU channel with UVM for memory access tracking | context setup | medium | ✓ |
| 2 | `/dev/nvidia-uvm` | `0x00000049` | UVM_MAP_EXTERNAL_SPARSE ⚠ | Map sparse external memory into UVM range | memory mapping | low | ✓ |
| 3 | `/dev/nvidia-uvm` | `0x00000021` | UVM_ALLOC_SEMAPHORE_POOL ⚠ | Allocate a semaphore pool for GPU synchronization | context setup | low | ✓ |
| 4 | `/dev/nvidiactl` | `0xC028465E` | NV_ESC_RM_DUP_OBJECT | Duplicate an RM object handle across clients/contexts | context setup | medium | ✓ |
| 5 | `/dev/nvidia-uvm` | `0x0000001B` | UVM_MAP_DYNAMIC_PARALLELISM_REGION ⚠ | Map a dynamic parallelism region in UVM for child kernel launches | context setup | low | ✓ |
| 6 | `/dev/nvidia-uvm` | `0x00000044` | UVM_SET_PREFERRED_LOCATION ⚠ | Set preferred memory location hint for a UVM allocation | memory management | low | ✓ |
| 7 | `/dev/nvidia-uvm` | `0x00000048` | UVM_CREATE_EXTERNAL_RANGE ⚠ | Create an external memory range within the UVM address space | context setup | low | ✓ |
| 8 | `/dev/nvidia0` | `0xC0384627` | NV_ESC_RM_SHARE | Share an RM resource between GPU contexts | context setup | medium | ✓ |
| 9 | `/dev/nvidia-uvm` | `0x00000041` | UVM_ENABLE_PEER_ACCESS | Enable peer-to-peer memory access between GPUs via UVM | context setup | medium | ✓ |
| 10 | `/dev/nvidia-uvm` | `0x00000022` | UVM_PAGEABLE_MEM_ACCESS ⚠ | Query/configure pageable memory access support in UVM | context setup | low | ✓ |
| 11 | `/dev/nvidia0` | `0xC01046CF` | NV_ESC_CHECK_VERSION_STR (variant) | Driver version check (alternate size variant) | initialization | medium | ✓ |
| 12 | `/dev/nvidiactl` | `0xC020464F` | NV_ESC_RM_MAP_MEMORY | Map GPU memory into the process virtual address space | memory mapping | high | ✓ |
| 13 | `/dev/nvidia-uvm` | `0x00000018` | UVM_UNREGISTER_GPU | Unregister a GPU from UVM | teardown | medium | ✓ |
| 14 | `/dev/nvidia-uvm` | `0x0000001C` | UVM_UNMAP_EXTERNAL ⚠ | Unmap an external allocation from the UVM address space | teardown | low | ✓ |
| 15 | `/dev/nvidia-uvm` | `0x0000001A` | UVM_UNREGISTER_CHANNEL | Unregister a GPU channel from UVM | context teardown | medium | ✓ |

### Event-level changes vs prev (frequency delta)

| Request Code | Name | Prev count | Cur count | Delta |
|-------------|------|-----------|----------|-------|
| `0x00000017` | UVM_MAP_EXTERNAL_ALLOCATION | 1 | 11 | ▲10 |
| `0x00000018` | UVM_UNREGISTER_GPU | 0 | 10 | ▲10 |
| `0x00000019` | UVM_REGISTER_CHANNEL | 0 | 1 | ▲1 |
| `0x0000001A` | UVM_UNREGISTER_CHANNEL | 0 | 1 | ▲1 |
| `0x0000001B` | UVM_MAP_DYNAMIC_PARALLELISM_REGION | 0 | 16 | ▲16 |
| `0x0000001C` | UVM_UNMAP_EXTERNAL | 0 | 16 | ▲16 |
| `0x00000021` | UVM_ALLOC_SEMAPHORE_POOL | 0 | 23 | ▲23 |
| `0x00000022` | UVM_PAGEABLE_MEM_ACCESS | 0 | 25 | ▲25 |
| `0x00000041` | UVM_ENABLE_PEER_ACCESS | 0 | 1 | ▲1 |
| `0x00000044` | UVM_SET_PREFERRED_LOCATION | 0 | 1 | ▲1 |
| `0x00000048` | UVM_CREATE_EXTERNAL_RANGE | 0 | 1 | ▲1 |
| `0x00000049` | UVM_MAP_EXTERNAL_SPARSE | 0 | 23 | ▲23 |
| `0xC00446C9` | NV_ESC_REGISTER_FD | 12 | 19 | ▲7 |
| `0xC0104629` | NV_ESC_RM_CONTROL | 5 | 96 | ▲91 |
| `0xC01046CE` | NV_ESC_CHECK_VERSION_STR | 4 | 11 | ▲7 |
| `0xC01046CF` | NV_ESC_CHECK_VERSION_STR (variant) | 0 | 7 | ▲7 |
| `0xC020462A` | NV_ESC_RM_ALLOC | 249 | 352 | ▲103 |
| `0xC020464F` | NV_ESC_RM_MAP_MEMORY | 0 | 23 | ▲23 |
| `0xC028465E` | NV_ESC_RM_DUP_OBJECT | 0 | 1 | ▲1 |
| `0xC030462B` | NV_ESC_RM_ALLOC (large) | 37 | 124 | ▲87 |
| `0xC0384627` | NV_ESC_RM_SHARE | 0 | 4 | ▲4 |
| `0xC038464E` | NV_ESC_RM_VID_HEAP_CONTROL | 4 | 27 | ▲23 |

---
