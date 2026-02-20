#!/usr/bin/env python3
"""
generate_report.py — render master_mapping.json as CUDA_IOCTL_MAP.md

W4: Two delta tables per step.
W7: Confidence summary table per step; ⚠ on low/none-confidence entries.
W9: Reproducibility column in new-ioctls table; repro status in properties.
"""
import json, os

BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE, "schema", "master_mapping.json")) as f:
    master = json.load(f)

lines = [
    "# CUDA → ioctl Mapping Report", "",
    "> **Environment:** Linux, CUDA 12.5 Driver API, strace-based",
    "> **Method:** Cumulative programs. Per-step metrics:",
    "> - *Code-set delta* — request codes not seen in any previous step",
    "> - *Event delta* — per-code frequency changes vs previous step",
    "> - *Confidence* — H=high / M=medium / L=low⚠ / N=none⚠ (low+none flagged for review)",
    "> - *Repro* — ✓ deterministic across runs / ⚠ R/N inconsistent / ? not checked",
    "", "---", "",
]

for call, data in master["cuda_to_ioctl_map"].items():

    # ── Reproducibility helpers (W9) ─────────────────────────────────────────
    repro       = data.get("reproducibility", {})
    repro_checked = repro.get("checked", False)
    repro_runs    = repro.get("runs", 0)
    non_det_codes = set(repro.get("non_deterministic_codes", []))
    occ_rates     = repro.get("code_occurrence_rate", {})

    def repro_cell(code):
        if not repro_checked: return "?"
        if code in non_det_codes:
            rate = occ_rates.get(code, 0)
            hits = round(rate * repro_runs)
            return f"⚠ {hits}/{repro_runs}"
        return "✓"

    # ── Properties table ─────────────────────────────────────────────────────
    cs = data.get("confidence_summary", {})
    needs_review_count = cs.get("low", 0) + cs.get("none", 0)
    repro_status = f"✓ ({repro_runs} runs)" if repro_checked else "not checked"

    lines += [
        f"## `{call}`", "",
        "| Property | Value |", "|----------|-------|",
        f"| Devices touched | `{', '.join(data['devices_touched'])}` |",
        f"| Total ioctls (cumulative) | {data['total_ioctls']} |",
        f"| Unique ioctl codes | {data['unique_codes']} |",
        f"| **New codes vs prev** | **{data['new_codes_vs_prev']}** |",
        f"| **Net new events vs prev** | **{data['net_new_events']}** |",
        f"| Reproducibility | {repro_status} |",
        "",
    ]

    # ── W7: Confidence summary ────────────────────────────────────────────────
    lines += [
        "#### Confidence summary (unique codes)",
        "| High | Medium | Low ⚠ | None ⚠ | Total flagged for review |",
        "|------|--------|--------|--------|--------------------------|",
        f"| {cs.get('high',0)} | {cs.get('medium',0)} | {cs.get('low',0)} "
        f"| {cs.get('none',0)} | {needs_review_count} |",
        "",
    ]

    # ── Code-set delta ────────────────────────────────────────────────────────
    seen, uniq = set(), []
    for i in data["new_ioctls_vs_prev"]:
        if i["request_code"] not in seen:
            seen.add(i["request_code"]); uniq.append(i)

    if uniq:
        lines += [
            "### New ioctls introduced (code-set delta)", "",
            "| # | Device | Request Code | Name | Description | Phase | Conf | Repro |",
            "|---|--------|-------------|------|-------------|-------|------|-------|",
        ]
        for idx, i in enumerate(uniq, 1):
            a     = i.get("annotation", {})
            conf  = a.get("confidence", "?")
            warn  = " ⚠" if a.get("needs_review") else ""
            desc  = a.get("description", "?").replace("|", "\\|")
            lines.append(
                f"| {idx} | `{i['device']}` | `{i['request_code']}` "
                f"| {a.get('name','?')}{warn} | {desc} "
                f"| {a.get('phase','?')} | {conf} | {repro_cell(i['request_code'])} |")
        lines.append("")
    else:
        lines += ["*No new ioctl codes introduced by this call.*", ""]

    # ── Event-level delta ─────────────────────────────────────────────────────
    ev = data.get("event_delta_vs_prev", {})
    if ev:
        # build name lookup from full_sequence annotations
        code_to_name = {}
        for i in data["full_sequence"]:
            if i["request_code"] not in code_to_name:
                code_to_name[i["request_code"]] = i.get("annotation", {}).get("name", "?")
        lines += [
            "### Event-level changes vs prev (frequency delta)", "",
            "| Request Code | Name | Prev count | Cur count | Delta |",
            "|-------------|------|-----------|----------|-------|",
        ]
        for code, counts in ev.items():
            arrow = "▲" if counts["delta"] > 0 else "▼"
            lines.append(
                f"| `{code}` | {code_to_name.get(code,'?')} "
                f"| {counts['prev_count']} | {counts['cur_count']} "
                f"| {arrow}{abs(counts['delta'])} |")
        lines.append("")
    else:
        lines += ["*No event-frequency changes vs previous step.*", ""]

    lines += ["---", ""]

out = os.path.join(BASE, "CUDA_IOCTL_MAP.md")
with open(out, "w") as f: f.write("\n".join(lines))
print(f"Report -> {out}")
