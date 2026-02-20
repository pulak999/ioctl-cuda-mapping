# CUDA → ioctl Mapping Report

> **Environment:** Linux, CUDA 12.5 Driver API, strace-based
> **Method:** Cumulative programs — delta per step = new ioctls introduced by that CUDA call

---

## `cu_init`

| Property | Value |
|----------|-------|
| Devices touched | `/dev/nvidia-uvm, /dev/nvidia0, /dev/nvidia1, /dev/nvidia2, /dev/nvidia3, /dev/nvidiactl` |
| Total ioctls (cumulative) | 333 |
| Unique ioctl codes | 16 |
| **New codes introduced** | **16** |

### New ioctls introduced

| # | Device | Request Code | Name | Description | Phase | Confidence |
|---|--------|-------------|------|-------------|-------|------------|
| 1 | `/dev/nvidiactl` | `0xC00846D6` | NV_ESC_CARD_INFO | Query basic GPU card information / card presence check | device query | medium |
| 2 | `/dev/nvidiactl` | `0xC90046C8` | NV_ESC_ATTACH_GPUS_TO_FD | Attach GPU devices to a file descriptor for subsequent RM calls | initialization | high |
| 3 | `/dev/nvidiactl` | `0xC020462B` | NV_ESC_RM_ALLOC_MEMORY | Allocate a memory object (device or system memory) via RM | memory allocation | high |
| 4 | `/dev/nvidiactl` | `0xC020462A` | NV_ESC_RM_ALLOC | Allocate an RM object (root client, device, subdevice, channel, context dma) | object allocation | high |
| 5 | `/dev/nvidiactl` | `0xC0104629` | NV_ESC_RM_CONTROL | Execute an RM control command on a GPU object (query/set properties) | object control | high |
| 6 | `/dev/nvidiactl` | `0xC030462B` | NV_ESC_RM_ALLOC (large) | Allocate RM object with larger parameter struct | object allocation | medium |
| 7 | `/dev/nvidia-uvm` | `0x30000001` | NV_UVM_INITIALIZE | Initialize the NVIDIA UVM (Unified Virtual Memory) driver | initialization | high |
| 8 | `/dev/nvidia-uvm` | `0x0000004B` | NV_ESC_RM_CONTROL (simple) | RM control call without size encoding (older ioctl form) | object control | low |
| 9 | `/dev/nvidia-uvm` | `0x00000027` | UVM_REGISTER_GPU_VASPACE | Register GPU virtual address space with UVM | initialization | medium |
| 10 | `/dev/nvidia0` | `0xC00446C9` | NV_ESC_REGISTER_FD | Register a file descriptor with the NVIDIA RM for GPU access | initialization | high |
| 11 | `/dev/nvidia0` | `0xC23046D7` | NV_ESC_NUMA_INFO | Query NUMA topology information for GPU memory | device query / initialization | medium |
| 12 | `/dev/nvidia0` | `0xC01046CE` | NV_ESC_CHECK_VERSION_STR | Check driver version string compatibility between userspace and kernel | initialization | high |
| 13 | `/dev/nvidiactl` | `0xC038464E` | NV_ESC_RM_VID_HEAP_CONTROL | Video heap (framebuffer/BAR1) memory management control | memory management | high |
| 14 | `/dev/nvidia-uvm` | `0x00000025` | UVM_REGISTER_GPU | Register a GPU with the UVM driver for unified memory management | initialization | medium |
| 15 | `/dev/nvidia-uvm` | `0x00000046` | NV_ESC_CARD_INFO (simple) | Query GPU card info (simple form without size encoding) | device query | medium |
| 16 | `/dev/nvidia-uvm` | `0x00000017` | UVM_MAP_EXTERNAL_ALLOCATION | Map an external (non-UVM managed) allocation into the UVM address space | memory mapping | medium |

---

## `cu_device_get`

| Property | Value |
|----------|-------|
| Devices touched | `/dev/nvidia-uvm, /dev/nvidia0, /dev/nvidia1, /dev/nvidia2, /dev/nvidia3, /dev/nvidiactl` |
| Total ioctls (cumulative) | 333 |
| Unique ioctl codes | 16 |
| **New codes introduced** | **0** |

*No new ioctls introduced by this call (delta = 0)*

---

## `cu_ctx_create`

| Property | Value |
|----------|-------|
| Devices touched | `/dev/nvidia-uvm, /dev/nvidia0, /dev/nvidia1, /dev/nvidia2, /dev/nvidia3, /dev/nvidiactl` |
| Total ioctls (cumulative) | 814 |
| Unique ioctl codes | 31 |
| **New codes introduced** | **15** |

### New ioctls introduced

| # | Device | Request Code | Name | Description | Phase | Confidence |
|---|--------|-------------|------|-------------|-------|------------|
| 1 | `/dev/nvidia-uvm` | `0x00000019` | UVM_REGISTER_CHANNEL | Register a GPU channel with UVM for memory access tracking | context setup | medium |
| 2 | `/dev/nvidia-uvm` | `0x00000049` | UVM_MAP_EXTERNAL_SPARSE | Map sparse external memory into UVM range | memory mapping | low |
| 3 | `/dev/nvidia-uvm` | `0x00000021` | UVM_ALLOC_SEMAPHORE_POOL | Allocate a semaphore pool for GPU synchronization | context setup | low |
| 4 | `/dev/nvidiactl` | `0xC028465E` | NV_ESC_RM_DUP_OBJECT | Duplicate an RM object handle across clients/contexts | context setup | medium |
| 5 | `/dev/nvidia-uvm` | `0x0000001B` | UVM_MAP_DYNAMIC_PARALLELISM_REGION | Map a dynamic parallelism region in UVM for child kernel launches | context setup | low |
| 6 | `/dev/nvidia-uvm` | `0x00000044` | UVM_SET_PREFERRED_LOCATION | Set preferred memory location hint for a UVM allocation | memory management | low |
| 7 | `/dev/nvidia-uvm` | `0x00000048` | UVM_CREATE_EXTERNAL_RANGE | Create an external memory range within the UVM address space | context setup | low |
| 8 | `/dev/nvidia0` | `0xC0384627` | NV_ESC_RM_SHARE | Share an RM resource between GPU contexts | context setup | medium |
| 9 | `/dev/nvidia-uvm` | `0x00000041` | UVM_ENABLE_PEER_ACCESS | Enable peer-to-peer memory access between GPUs via UVM | context setup | medium |
| 10 | `/dev/nvidia-uvm` | `0x00000022` | UVM_PAGEABLE_MEM_ACCESS | Query/configure pageable memory access support in UVM | context setup | low |
| 11 | `/dev/nvidia0` | `0xC01046CF` | NV_ESC_CHECK_VERSION_STR (variant) | Driver version check (alternate size variant) | initialization | medium |
| 12 | `/dev/nvidiactl` | `0xC020464F` | NV_ESC_RM_MAP_MEMORY | Map GPU memory into the process virtual address space | memory mapping | high |
| 13 | `/dev/nvidia-uvm` | `0x00000018` | UVM_UNREGISTER_GPU | Unregister a GPU from UVM | teardown | medium |
| 14 | `/dev/nvidia-uvm` | `0x0000001C` | UVM_UNMAP_EXTERNAL | Unmap an external allocation from the UVM address space | teardown | low |
| 15 | `/dev/nvidia-uvm` | `0x0000001A` | UVM_UNREGISTER_CHANNEL | Unregister a GPU channel from UVM | context teardown | medium |

---
