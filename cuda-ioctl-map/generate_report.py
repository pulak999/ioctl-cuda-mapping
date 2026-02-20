#!/usr/bin/env python3
import json, os

BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE,"schema","master_mapping.json")) as f: master = json.load(f)

lines = ["# CUDA → ioctl Mapping Report","",
         "> **Environment:** Linux, CUDA 12.5 Driver API, strace-based",
         "> **Method:** Cumulative programs — delta per step = new ioctls introduced by that CUDA call","","---",""]

for call, data in master["cuda_to_ioctl_map"].items():
    lines += [f"## `{call}`","",
              "| Property | Value |","|----------|-------|",
              f"| Devices touched | `{', '.join(data['devices_touched'])}` |",
              f"| Total ioctls (cumulative) | {data['total_ioctls']} |",
              f"| Unique ioctl codes | {data['unique_codes']} |",
              f"| **New codes introduced** | **{data['new_codes_vs_prev']}** |",""]
    seen, uniq = set(), []
    for i in data["new_ioctls_vs_prev"]:
        if i["request_code"] not in seen:
            seen.add(i["request_code"]); uniq.append(i)
    if uniq:
        lines += ["### New ioctls introduced","",
                  "| # | Device | Request Code | Name | Description | Phase | Confidence |",
                  "|---|--------|-------------|------|-------------|-------|------------|"]
        for idx, i in enumerate(uniq, 1):
            a    = i.get("annotation", {})
            rv   = " WARNING" if a.get("needs_review") else ""
            desc = a.get("description", "?").replace("|", "\\|")
            name = a.get("name", "?")
            phase = a.get("phase", "?")
            conf  = a.get("confidence", "?")
            dev   = i["device"]
            code  = i["request_code"]
            lines.append(f"| {idx} | `{dev}` | `{code}` | {name}{rv} | {desc} | {phase} | {conf} |")
        lines.append("")
    else:
        lines += ["*No new ioctls introduced by this call (delta = 0)*",""]
    lines += ["---",""]

out = os.path.join(BASE, "CUDA_IOCTL_MAP.md")
with open(out,"w") as f: f.write("\n".join(lines))
print(f"Report -> {out}")
