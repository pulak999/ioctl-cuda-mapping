#!/usr/bin/env python3
import json, os, glob

BASE = os.path.dirname(__file__)
STEP_ORDER = ["cu_init","cu_device_get","cu_ctx_create","cu_mem_alloc","cu_memcpy_htod",
              "cu_launch_kernel","cu_memcpy_dtoh","cu_mem_free","cu_ctx_destroy"]
all_f  = {os.path.basename(f).replace(".json",""):f for f in glob.glob(os.path.join(BASE,"annotated","*.json"))}
FILES  = [all_f[s] for s in STEP_ORDER if s in all_f]
FILES += [f for f in sorted(all_f.values()) if f not in FILES]

master = {"cuda_to_ioctl_map": {}}
prev_codes = set()
for fpath in FILES:
    with open(fpath) as f: data = json.load(f)
    call = data["cuda_call"]
    cur  = {i["request_code"] for i in data["ioctl_sequence"]}
    new  = cur - prev_codes
    master["cuda_to_ioctl_map"][call] = {
        "devices_touched": sorted(set(data["fd_map"].values())),
        "total_ioctls": len(data["ioctl_sequence"]),
        "unique_codes": len(cur), "new_codes_vs_prev": len(new),
        "new_ioctls_vs_prev": [i for i in data["ioctl_sequence"] if i["request_code"] in new and i["is_new"]],
        "full_sequence": data["ioctl_sequence"],
    }
    prev_codes = cur

out = os.path.join(BASE,"schema","master_mapping.json")
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out,"w") as f: json.dump(master,f,indent=2)
print(f"Schema → {out}")
for c,d in master["cuda_to_ioctl_map"].items():
    print(f"  [{c}] total={d['total_ioctls']} unique={d['unique_codes']} new={d['new_codes_vs_prev']}")
