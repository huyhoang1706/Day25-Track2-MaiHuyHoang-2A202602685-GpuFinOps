import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops import pricing
from missions import m6_extensions


def test_cache_is_worth_it():
    assert pricing.cache_is_worth_it(5, 0.30, 0.10, price_in_per_m=3.0)
    assert not pricing.cache_is_worth_it(1, 0.30, 0.10, price_in_per_m=0.20)   # cheap model, few reads
    assert abs(pricing.cache_break_even_reads(0.27, 3.0, 0.10) - 0.1) < 1e-9


def test_reasoning_is_energy_heavy():
    r = m6_extensions.reasoning_budget()
    assert r["reasoning_wh_pct"] > r["reasoning_usd_pct"] > r["reasoning_traffic_pct"]
    assert r["wh_saved_pct"] > 0


def test_carbon_scheduling_and_rightsize():
    c = m6_extensions.carbon_scheduling()
    assert c["cleanest"] == "europe-north1" and c["kg_saved"] > 0
    assert m6_extensions.rightsize_mbu()["monthly_savings"] > 0
