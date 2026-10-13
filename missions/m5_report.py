"""M5 — Optimization Report: combine M1-M4 into baseline-vs-optimized (deck §1/§11).

Run: python missions/m5_report.py   ->  outputs/report.md + outputs/savings.png
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os
from missions._common import num, catalog_by_type, ROOT
from finops import report, sustainability
from missions import m1_efficiency_audit, m2_inference_levers, m3_purchasing, m6_extensions

DAYS = 30
# one tier down for over-provisioned ("util-lie") GPUs
RIGHTSIZE_MAP = {"H100": "A100", "H200": "H100", "A100": "A10G", "A10G": "L4", "L4": "L4"}


def _analysis(r1, r2, r3, levers, baseline, optimized, ext) -> list[str]:
    """Narrative sections: unit economics, GPU-Util lie, prioritised actions, sustainability."""
    lies = r1["lies"]
    lie_txt = ", ".join(f"`{l['gpu_id']}` ({l['gpu_type']}, util {l['gpu_util_pct']}%, MFU {l['mfu']:.0%})" for l in lies)
    rb, cs, ca, rs = ext["reasoning"], ext["carbon"], ext["cache"], ext["rightsize"]
    ranked = sorted(levers.items(), key=lambda kv: -kv[1])
    tot = sum(levers.values())
    unit = (f"## Unit economics\n\n- Inference: **${r2['baseline_per_m']:.3f} -> ${r2['optimized_per_m']:.3f} per 1M tokens** "
            f"({r2['savings_pct']}% lower) from cascade + prompt cache + batch.\n"
            f"- Purchasing: on-demand ${r3['on_demand_monthly']:,} -> ${r3['optimized_monthly']:,}/month ({r3['savings_pct']}%).\n"
            f"- Blended: ${baseline:,.0f} -> ${optimized:,.0f}/month ({tot/baseline:.1%}).")
    lie_sec = ("## Why GPU-Util lies\n\n"
               f"Flagged: {lie_txt}.\n\n"
               "`nvidia-smi` GPU-Util only records the share of sample periods in which *at least one kernel* was resident - "
               "it says nothing about how many SMs were busy or how many FLOPs retired. A GPU stalled on HBM reads "
               "(memory-bound decode, small batch), waiting on host/PCIe/NCCL, or running tiny kernels still shows ~100%. "
               "MFU (achieved/peak FLOPs) and MBU expose it. Financially, you pay the full $/hr for a card delivering a "
               "fraction of its useful work, so effective $/token is several times the sticker price; the fix is batching/"
               "right-sizing, not buying more GPUs. Right-sizing these cards saves "
               f"${levers['Right-size util-lies']:,}/month; a bandwidth-based check (MBU, target 60%) "
               f"finds ${rs['monthly_savings']:,}/month using cheaper cards that still fit the working set.")
    acts = ["## Prioritised actions", "", "| # | Action | Monthly impact | Why this order |", "|---|---|---|---|"]
    why = {"Purchasing (spot/reserved)": "largest $, no code change; checkpoint training jobs first",
           "Inference (cascade/cache/batch)": "software-only, quality-gated; scales with traffic",
           "Right-size util-lies": "needs benchmarking per model, moderate risk",
           "Kill idle GPUs": "trivial to do (autoscale-to-zero) but small $"}
    for i, (n, v) in enumerate(ranked, 1):
        acts.append(f"| {i} | {n} | ${v:,} ({v/tot:.0%} of savings) | {why[n]} |")
    sus = ("## Sustainability notes\n\n"
           f"- Reasoning queries are {rb['reasoning_traffic_pct']}% of traffic but {rb['reasoning_usd_pct']}% of spend and "
           f"**{rb['reasoning_wh_pct']}% of energy** ({rb['wh_per_query']['reasoning']} Wh vs {rb['wh_per_query']['normal']} Wh per query, ~80x). "
           f"Capping them at {rb['cap_frac']:.0%} (route only low-confidence/hard tasks) cuts energy {rb['wh_saved_pct']}%.\n"
           f"- Moving the {cs['interruptible_jobs']} interruptible jobs ({cs['kwh']:,} kWh) from us-east-1 to `{cs['cleanest']}` removes "
           f"{cs['kg_saved']} kgCO2 ({cs['pct_saved']}%); electricity is ${cs['regions']['us-east-1']['energy_usd']:,} -> "
           f"${cs['regions'][cs['cleanest']]['energy_usd']:,}. Cheapest is `{cs['cheapest']}`, best cost/carbon balance `{cs['balanced']}`; "
           "the cleanest region is far from US users, so use it only for latency-insensitive training/batch, not interactive serving.\n"
           f"- Prompt caching pays off immediately (avg {ca['avg_reads_per_prefix']} reads/prefix vs break-even "
           f"{ca['tiers']['small']['break_even_reads']} on the small tier, {ca['tiers']['large']['break_even_reads']} on the large tier).")
    return [unit, lie_sec, "\n".join(acts), sus]


def run(verbose: bool = True) -> dict:
    r1 = m1_efficiency_audit.run(verbose=False)
    r2 = m2_inference_levers.run(verbose=False)
    r3 = m3_purchasing.run(verbose=False)
    cat = catalog_by_type()

    # --- buckets ---
    infer_savings = (r2["baseline_daily"] - r2["optimized_daily"]) * DAYS
    purchasing_savings = r3["on_demand_monthly"] - r3["optimized_monthly"]

    idle_savings = r1["idle_waste_daily"] * DAYS
    rightsize_savings = 0.0
    for lie in r1["lies"]:
        cur = lie["gpu_type"]
        tgt = RIGHTSIZE_MAP.get(cur, cur)
        delta = num(cat[cur]["on_demand_hr"]) - num(cat[tgt]["on_demand_hr"])
        rightsize_savings += max(0.0, delta) * 24 * DAYS

    levers = {
        "Inference (cascade/cache/batch)": round(infer_savings),
        "Purchasing (spot/reserved)": round(purchasing_savings),
        "Right-size util-lies": round(rightsize_savings),
        "Kill idle GPUs": round(idle_savings),
    }
    baseline = r2["baseline_daily"] * DAYS + r3["on_demand_monthly"]
    optimized = baseline - sum(levers.values())
    total_pct = sum(levers.values()) / baseline * 100 if baseline else 0.0

    # --- sustainability snapshot ---
    median_tokens = 800
    wh = sustainability.wh_per_query(median_tokens)
    sust = {
        "wh_per_query": wh,
        "carbon_g": sustainability.carbon_g(wh, "us-east-1"),
        "best_region": min(sustainability.REGION_CARBON, key=sustainability.REGION_CARBON.get),
    }

    ext = m6_extensions.run(verbose=False)
    md = report.build_report(baseline, optimized, levers, sustainability=sust,
                             extra_sections=_analysis(r1, r2, r3, levers, baseline, optimized, ext))
    out_md = os.path.join(ROOT, "outputs", "report.md")
    os.makedirs(os.path.dirname(out_md), exist_ok=True)
    with open(out_md, "w") as f:
        f.write(md)
    png = report.savings_waterfall(levers, os.path.join(ROOT, "outputs", "savings.png"))

    if verbose:
        print("== M5 Optimization Report ==")
        print(md)
        print(f"\nWritten: outputs/report.md" + (f" + outputs/savings.png" if png else " (matplotlib absent: PNG skipped)"))

    return {"baseline_monthly": round(baseline), "optimized_monthly": round(optimized),
            "levers": levers, "total_savings_pct": round(total_pct, 1)}


if __name__ == "__main__":
    run()
