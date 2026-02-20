#!/usr/bin/env python3
import json, sys, os

BASE = os.path.dirname(__file__)
with open(os.path.join(BASE,"lookup","ioctl_table.json")) as f: LOOKUP = json.load(f)

def annotate(parsed_path):
    with open(parsed_path) as f: data = json.load(f)
    unknown = []
    for i in data["ioctl_sequence"]:
        c = i["request_code"]
        if c in LOOKUP:
            i["annotation"] = dict(LOOKUP[c]); i["annotation"]["needs_review"] = False
        else:
            i["annotation"] = {"name":"UNKNOWN","description":"","phase":"","confidence":"none","needs_review":True}
            unknown.append(c)
    out_dir  = os.path.join(os.path.dirname(os.path.dirname(parsed_path)), "annotated")
    out_path = os.path.join(out_dir, os.path.basename(parsed_path))
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path,"w") as f: json.dump(data,f,indent=2)
    unk_u = sorted(set(unknown))
    print(f"[{data['cuda_call']}] known={len(data['ioctl_sequence'])-len(unknown)} unknown={len(unk_u)} -> {out_path}")
    for c in unk_u: print(f"  ? {c}")
    return out_path, unk_u

if __name__ == "__main__":
    all_unk = {}
    for p in sys.argv[1:]:
        _, u = annotate(p)
        if u: all_unk[os.path.basename(p)] = u
    if all_unk:
        print("\nCODES NEEDING REVIEW:")
        for s,codes in all_unk.items():
            print(f"  [{s}]"); [print(f"    {c}") for c in codes]
    else:
        print("\nAll ioctls annotated — no unknowns!")
