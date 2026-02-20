#!/usr/bin/env python3
"""
build_schema.py — aggregate annotated per-step JSONs into master_mapping.json

W4: Two distinct delta metrics per step.
W7: confidence_summary — count of unique codes at each confidence tier per step.
W9: reproducibility data merged from <step>_repro_report.json if present.
"""
import json, os, glob
from collections import Counter

BASE = os.path.dirname(__file__)
STEP_ORDER = ["cu_init","cu_device_get","cu_ctx_create","cu_mem_alloc","cu_memcpy_htod",
              "cu_launch_kernel","cu_memcpy_dtoh","cu_mem_free","cu_ctx_destroy"]
all_f  = {os.path.basename(f).replace(".json",""):f for f in glob.glob(os.path.join(BASE,"annotated","*.json"))}
FILES  = [all_f[s] for s in STEP_ORDER if s in all_f]
FILES += [f for f in sorted(all_f.values()) if f not in FILES]

master      = {"cuda_to_ioctl_map": {}}
prev_codes  = set()
prev_counts = Counter()

for fpath in FILES:
    with open(fpath) as f: data = json.load(f)
    call = data["cuda_call"]

    cur_codes  = {i["request_code"] for i in data["ioctl_sequence"]}
    cur_counts = Counter(i["request_code"] for i in data["ioctl_sequence"])
    new_codes  = cur_codes - prev_codes

    # W4: per-code frequency delta
    event_delta = {}
    for code in sorted(cur_codes | set(prev_counts)):
        c = cur_counts.get(code, 0)
        p = prev_counts.get(code, 0)
        if c != p:
            event_delta[code] = {"prev_count": p, "cur_count": c, "delta": c - p}

    # W7: confidence summary — count each unique code once at its confidence tier
    conf_summary = Counter()
    seen_conf = {}
    for i in data["ioctl_sequence"]:
        rc = i["request_code"]
        if rc not in seen_conf:
            tier = i.get("annotation", {}).get("confidence", "none")
            seen_conf[rc] = tier
            conf_summary[tier] += 1
    # always emit all four canonical tiers so consumers can rely on the keys
    confidence_summary = {
        "high":   conf_summary.get("high",   0),
        "medium": conf_summary.get("medium", 0),
        "low":    conf_summary.get("low",    0),
        "none":   conf_summary.get("none",   0),
    }

    # W9: merge reproducibility report if one exists
    repro_path = os.path.join(BASE, "parsed", f"{call}_repro_report.json")
    if os.path.exists(repro_path):
        with open(repro_path) as f: repro = json.load(f)
    else:
        repro = {"checked": False}

    master["cuda_to_ioctl_map"][call] = {
        "devices_touched":     sorted(set(data["fd_map"].values())),
        "total_ioctls":        len(data["ioctl_sequence"]),
        "unique_codes":        len(cur_codes),
        # ── code-set delta (W4) ──────────────────────────────────────────────
        "new_codes_vs_prev":   len(new_codes),
        "new_ioctls_vs_prev":  [i for i in data["ioctl_sequence"]
                                 if i["request_code"] in new_codes and i["is_new"]],
        # ── event-level delta (W4) ───────────────────────────────────────────
        "net_new_events":      len(data["ioctl_sequence"]) - sum(prev_counts.values()),
        "event_delta_vs_prev": event_delta,
        # ── confidence summary (W7) ──────────────────────────────────────────
        "confidence_summary":  confidence_summary,
        # ── reproducibility (W9) ─────────────────────────────────────────────
        "reproducibility":     repro,
        # ── full sequence ─────────────────────────────────────────────────────
        "full_sequence":       data["ioctl_sequence"],
    }
    prev_codes  = cur_codes
    prev_counts = cur_counts

out = os.path.join(BASE, "schema", "master_mapping.json")
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w") as f: json.dump(master, f, indent=2)
print(f"Schema → {out}")
for c, d in master["cuda_to_ioctl_map"].items():
    cs = d["confidence_summary"]
    rep = "✓" if d["reproducibility"].get("checked") else "not checked"
    print(f"  [{c}] total={d['total_ioctls']} unique={d['unique_codes']} "
          f"new_codes={d['new_codes_vs_prev']} net_new_events={d['net_new_events']} "
          f"conf=H{cs['high']}/M{cs['medium']}/L{cs['low']}/N{cs['none']} repro={rep}")
