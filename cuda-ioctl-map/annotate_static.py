#!/usr/bin/env python3
"""
annotate_static.py — look up ioctl request codes in lookup/ioctl_table.json
and attach annotation objects to every ioctl record in a parsed JSON.

W7 — Confidence discipline
  needs_review is set to True for three categories:
    1. UNKNOWN  — code not in lookup table at all
    2. low      — code is in the table but confidence is "low"
    3. none     — code exists in table with explicit confidence="none"
  "medium" entries are trusted by default but their annotations are visible in
  the report.  "high" entries are treated as ground truth.
"""
import json, sys, os

BASE = os.path.dirname(__file__)
with open(os.path.join(BASE,"lookup","ioctl_table.json")) as f: LOOKUP = json.load(f)

# W7: confidence tiers that warrant flagging for human review
LOW_CONFIDENCE = {"low", "none"}

def annotate(parsed_path):
    with open(parsed_path) as f: data = json.load(f)
    unknown, low_conf = [], []
    for i in data["ioctl_sequence"]:
        c = i["request_code"]
        if c in LOOKUP:
            ann = dict(LOOKUP[c])
            needs_review = ann.get("confidence", "none") in LOW_CONFIDENCE   # W7
            ann["needs_review"] = needs_review
            i["annotation"] = ann
            if needs_review:
                low_conf.append(c)
        else:
            i["annotation"] = {
                "name": "UNKNOWN", "description": "", "phase": "",
                "confidence": "none", "needs_review": True,
            }
            unknown.append(c)

    out_dir  = os.path.join(os.path.dirname(os.path.dirname(parsed_path)), "annotated")
    out_path = os.path.join(out_dir, os.path.basename(parsed_path))
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w") as f: json.dump(data, f, indent=2)

    unk_u = sorted(set(unknown))
    lc_u  = sorted(set(low_conf))
    print(f"[{data['cuda_call']}] known={len(data['ioctl_sequence'])-len(unknown)} "
          f"unknown={len(unk_u)} low_conf_unique={len(lc_u)} -> {out_path}")
    for c in unk_u: print(f"  ? {c}")
    return out_path, unk_u

if __name__ == "__main__":
    all_unk = {}
    for p in sys.argv[1:]:
        _, u = annotate(p)
        if u: all_unk[os.path.basename(p)] = u
    if all_unk:
        print("\nCODES NEEDING REVIEW (unknown):")
        for s, codes in all_unk.items():
            print(f"  [{s}]"); [print(f"    {c}") for c in codes]
    else:
        print("\nAll ioctls annotated — no unknowns!")
