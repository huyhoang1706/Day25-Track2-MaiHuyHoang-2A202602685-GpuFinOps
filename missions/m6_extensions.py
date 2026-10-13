"""M6 — "Your Turn" extensions 2-5: MBU right-sizing, cache break-even,
reasoning budget, carbon-aware scheduling.

Run: python missions/m6_extensions.py
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from collections import defaultdict
from missions._common import load_csv, num, catalog_by_type
from missions import m1_efficiency_audit
from missions.m2_inference_levers import MODEL_PRICES
from finops import pricing, sustainability

DAYS = 30
CACHE_WRITE_PREMIUM = 0.25     # Anthropic cache write = 1.25x input -> +0.25x extra over a normal read
MBU_TARGET = 0.60              # healthy decode bandwidth utilization


# ---------------------------------------------------------------- Ext 2
def rightsize_mbu() -> dict:
    """Memory-bound GPUs: pick the cheapest GPU that fits the working set and
    whose peak bandwidth still reaches the same achieved GB/s at MBU_TARGET."""
    cat = catalog_by_type()
    summary = m1_efficiency_audit.run(verbose=False)["summary"]
    tel = load_csv("gpu_telemetry.csv")
    peak_mem, bw = defaultdict(float), defaultdict(list)
    for r in tel:
        peak_mem[r["gpu_id"]] = max(peak_mem[r["gpu_id"]], num(r["mem_used_gb"]))
        bw[r["gpu_id"]].append(num(r["achieved_bw_tbs"]))
    per_gb = {t: num(c["on_demand_hr"]) / num(c["hbm_gb"]) for t, c in cat.items()}

    rows, monthly = [], 0.0
    for s in summary:
        if s["mbu"] >= 0.30 or s["gpu_util_pct"] < 10:   # not memory-bound (or idle: handled elsewhere)
            continue
        cur = cat[s["gpu_type"]]
        need_bw = max(bw[s["gpu_id"]])                     # TB/s actually pulled at peak
        best = None
        for t, c in cat.items():
            fits = num(c["hbm_gb"]) >= peak_mem[s["gpu_id"]] and num(c["peak_bw_tbs"]) * MBU_TARGET >= need_bw
            if fits and num(c["on_demand_hr"]) < num(cur["on_demand_hr"]):
                if best is None or num(c["on_demand_hr"]) < num(cat[best]["on_demand_hr"]):
                    best = t
        if best:
            delta = num(cur["on_demand_hr"]) - num(cat[best]["on_demand_hr"])
            monthly += delta * 24 * DAYS
            rows.append({"gpu_id": s["gpu_id"], "from": s["gpu_type"], "to": best, "mbu": s["mbu"],
                         "usd_gb": round(per_gb[s["gpu_type"]], 3), "to_usd_gb": round(per_gb[best], 3),
                         "save_pct": round(delta / num(cur["on_demand_hr"]) * 100, 1)})
    return {"per_gb_vram": {t: round(v, 3) for t, v in per_gb.items()},
            "recommendations": rows, "monthly_savings": round(monthly)}


# ---------------------------------------------------------------- Ext 3
def cache_analysis() -> dict:
    rows = load_csv("token_usage.csv")
    # reuse proxy: requests sharing a (project, model) prefix; reads = hits per prefix - 1 write
    hits, in_tok = defaultdict(int), defaultdict(float)
    for r in rows:
        if num(r["cached_input_tokens"]) > 0:
            hits[(r["project"], r["route_tier"])] += 1
    avg_reads = (sum(hits.values()) / len(hits)) - 1 if hits else 0.0
    tiers = {}
    for tier, (pin, _) in MODEL_PRICES.items():
        be = pricing.cache_break_even_reads(CACHE_WRITE_PREMIUM * pin, pin)
        tiers[tier] = {"price_in": pin, "break_even_reads": round(be, 2),
                       "worth_it": pricing.cache_is_worth_it(avg_reads, CACHE_WRITE_PREMIUM * pin, 0.10, pin)}
    return {"avg_reads_per_prefix": round(avg_reads, 1), "tiers": tiers}


# ---------------------------------------------------------------- Ext 4
def reasoning_budget(cap_frac: float = 0.03) -> dict:
    rows = load_csv("token_usage.csv")
    acc = {0: [0, 0.0, 0.0, 0], 1: [0, 0.0, 0.0, 0]}   # n, usd, wh, tokens
    for r in rows:
        inp, out = int(num(r["input_tokens"])), int(num(r["output_tokens"]))
        flag = int(num(r["is_reasoning"]))
        pin, pout = MODEL_PRICES[r["route_tier"]]
        usd = pricing.request_cost(inp, out, pin, pout, cached_in=int(num(r["cached_input_tokens"])),
                                   batch=bool(int(num(r["is_batch"]))))
        wh = sustainability.wh_per_query(inp + out, is_reasoning=bool(flag))
        a = acc[flag]; a[0] += 1; a[1] += usd; a[2] += wh; a[3] += inp + out
    n, usd, wh = (sum(acc[k][i] for k in acc) for i in (0, 1, 2))
    share = acc[1][0] / n
    # cap: demote the excess reasoning requests to normal queries (same tokens, same $)
    keep = min(1.0, cap_frac / share) if share else 1.0
    wh_saved = acc[1][2] * (1 - keep) * (1 - 1 / sustainability.REASONING_ENERGY_MULTIPLIER)
    return {"reasoning_traffic_pct": round(share * 100, 1),
            "reasoning_usd_pct": round(acc[1][1] / usd * 100, 1),
            "reasoning_wh_pct": round(acc[1][2] / wh * 100, 1),
            "wh_per_query": {"reasoning": round(acc[1][2] / acc[1][0], 2) if acc[1][0] else 0,
                             "normal": round(acc[0][2] / acc[0][0], 3)},
            "cap_frac": cap_frac, "wh_saved_pct": round(wh_saved / wh * 100, 1),
            "wh_saved_daily": round(wh_saved, 1)}


# ---------------------------------------------------------------- Ext 5
def carbon_scheduling() -> dict:
    cat = catalog_by_type()
    jobs = [j for j in load_csv("workloads.csv") if int(num(j["interruptible"])) == 1]
    kwh = 0.0
    for j in jobs:
        gpu_h = num(j["hours_per_day"]) * num(j["days"]) * int(num(j["num_gpus"]))
        kwh += gpu_h * num(cat[j["gpu_type"]]["watts"]) / 1000.0
    regions = {}
    for reg in sustainability.REGION_CARBON:
        regions[reg] = {"usd_kwh": sustainability.REGION_PRICE_KWH[reg],
                        "g_kwh": sustainability.REGION_CARBON[reg],
                        "energy_usd": round(kwh * sustainability.REGION_PRICE_KWH[reg], 2),
                        "kg_co2": round(kwh * sustainability.REGION_CARBON[reg] / 1000, 1)}
    cheapest = min(regions, key=lambda r: regions[r]["energy_usd"])
    cleanest = min(regions, key=lambda r: regions[r]["kg_co2"])
    # balanced: min of normalized (cost + carbon)
    mx_c = max(v["energy_usd"] for v in regions.values()); mx_g = max(v["kg_co2"] for v in regions.values())
    balanced = min(regions, key=lambda r: regions[r]["energy_usd"] / mx_c + regions[r]["kg_co2"] / mx_g)
    base, best = regions["us-east-1"]["kg_co2"], regions[cleanest]["kg_co2"]
    return {"interruptible_jobs": len(jobs), "kwh": round(kwh), "regions": regions,
            "cheapest": cheapest, "cleanest": cleanest, "balanced": balanced,
            "kg_saved": round(base - best, 1), "pct_saved": round((base - best) / base * 100, 1)}


def run(verbose: bool = True) -> dict:
    res = {"rightsize": rightsize_mbu(), "cache": cache_analysis(),
           "reasoning": reasoning_budget(), "carbon": carbon_scheduling()}
    if verbose:
        rs, ca, rb, cs = res["rightsize"], res["cache"], res["reasoning"], res["carbon"]
        print("== M6 Extensions ==")
        print("\n[Ext2] $/GB-VRAM:", rs["per_gb_vram"])
        for r in rs["recommendations"]:
            print(f"  {r['gpu_id']}: {r['from']} -> {r['to']} (MBU {r['mbu']}, "
                  f"${r['usd_gb']}->${r['to_usd_gb']}/GB, -{r['save_pct']}%)")
        print(f"  monthly savings if all right-sized: ${rs['monthly_savings']:,}")
        print(f"\n[Ext3] avg reads/prefix = {ca['avg_reads_per_prefix']}")
        for t, v in ca["tiers"].items():
            print(f"  {t}: break-even {v['break_even_reads']} reads -> worth_it={v['worth_it']}")
        print(f"\n[Ext4] reasoning = {rb['reasoning_traffic_pct']}% traffic, {rb['reasoning_usd_pct']}% $, "
              f"{rb['reasoning_wh_pct']}% Wh  (Wh/query {rb['wh_per_query']})")
        print(f"  capping at {rb['cap_frac']:.0%} saves {rb['wh_saved_pct']}% energy ({rb['wh_saved_daily']} Wh/day)")
        print(f"\n[Ext5] {cs['interruptible_jobs']} interruptible jobs, {cs['kwh']:,} kWh")
        print(f"  {'region':16}{'$/kWh':>7}{'g/kWh':>7}{'energy $':>11}{'kgCO2':>9}")
        for r, v in cs["regions"].items():
            print(f"  {r:16}{v['usd_kwh']:>7}{v['g_kwh']:>7}{v['energy_usd']:>11,}{v['kg_co2']:>9,}")
        print(f"  cheapest={cs['cheapest']} cleanest={cs['cleanest']} balanced={cs['balanced']}; "
              f"us-east-1 -> {cs['cleanest']} saves {cs['kg_saved']} kgCO2 ({cs['pct_saved']}%)")
    return res


if __name__ == "__main__":
    run()
