#!/usr/bin/env python3
"""
generate_report.py — render master_mapping.json as CUDA_IOCTL_MAP.md

W4: Two delta tables per step.
W7: Confidence summary table per step; ⚠ on low/none-confidence entries.
W9: Reproducibility column in new-ioctls table; repro status in properties.
"""
import json, os

BASE = os.path.dirname(os.path.abspath(__file__))

def _md_escape(s: str) -> str:
    """G3-fix: escape Markdown special characters that can break table cells."""
    s = s.replace("|",  "\\|")   # column separator — was the only one escaped before
    s = s.replace("*",  "\\*")   # bold / italic
    s = s.replace("`",  "\\`")   # inline code fence
    s = s.replace("[",  "\\[")   # link / footnote open bracket
    return s
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
    repro         = data.get("reproducibility", {})
    repro_checked = repro.get("checked", False)
    repro_runs    = repro.get("runs", 0)
    non_det_codes = set(repro.get("non_deterministic_codes", []))
    occ_rates     = repro.get("code_occurrence_rate", {})
    # XC4-fix: pull frequency-stability fields added by C1 fix
    freq_unstable      = repro.get("frequency_unstable_codes", {})   # code → {min,max,per_run}
    freq_stab_score    = repro.get("frequency_stability_score")       # float or None if old report

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
    # XC4-fix: frequency stability status line
    if not repro_checked or freq_stab_score is None:
        freq_status = "not checked"
    elif freq_unstable:
        n_unstable = len(freq_unstable)
        n_total    = len(occ_rates)
        freq_status = f"⚠ {freq_stab_score:.2%} ({n_unstable}/{n_total} codes vary in count)"
    else:
        freq_status = f"✓ {freq_stab_score:.2%} (all codes fire stable count)"

    lines += [
        f"## `{call}`", "",
        "| Property | Value |", "|----------|-------|",
        f"| Devices touched | `{', '.join(data['devices_touched'])}` |",
        f"| Total ioctls (cumulative) | {data['total_ioctls']} |",
        f"| Unique ioctl codes | {data['unique_codes']} |",
        f"| **New codes vs prev** | **{data['new_codes_vs_prev']}** |",
        f"| **Net event delta vs prev** | **{data['net_event_delta']}** |",
        f"| Presence reproducibility | {repro_status} |",
        f"| Frequency stability | {freq_status} |",   # XC4-fix
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

    # ── XC4-fix: Frequency-unstable codes detail table ────────────────────────
    if repro_checked and freq_unstable:
        # build name lookup from full_sequence
        code_to_name_freq = {}
        for i in data.get("full_sequence", []):
            rc = i["request_code"]
            if rc not in code_to_name_freq:
                code_to_name_freq[rc] = i.get("annotation", {}).get("name", "?")
        lines += [
            "#### Frequency-unstable codes ⚠ (present every run, count varies)",
            "",
            "| Request Code | Name | Min | Max | Per-run counts |",
            "|-------------|------|-----|-----|----------------|",
        ]
        for code, info in sorted(freq_unstable.items()):
            per_run_str = ", ".join(str(x) for x in info["per_run"])
            lines.append(
                f"| `{code}` | {code_to_name_freq.get(code, '?')} "
                f"| {info['min']} | {info['max']} | {per_run_str} |")
        lines.append("")

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
            desc  = _md_escape(a.get("description", "?"))
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
