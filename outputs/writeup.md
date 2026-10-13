# Write-up — Lab 25 GPU FinOps (NimbusAI)

1. **Baseline vs optimized:** $27,622 -> $14,697/month (**-46.8%**). Inference unit cost $8.651 -> $1.436 per 1M tokens (-83.4%). Prices: small = Claude Haiku 5.5 ($0.10/$0.50 per 1M), large = Claude Opus 5.5 ($4/$20); cache read 0.1x, cache write 1.25x, batch -50%.
2. **Levers:** Purchasing (spot/reserved) $10,040 (80% of savings) > inference $1,630 > right-size $655 > idle $600. Purchasing dominates because training GPUs are the bulk of spend and most jobs are interruptible/steady.
3. **GPU-Util lie:** `gpu-h100-4` (util 98%, MFU 19%) and `gpu-a10g-1` (util 97%, MFU 27%). GPU-Util only means "a kernel was resident"; stalls on HBM/PCIe/NCCL still count as busy. Right-sizing saves $655/month (tier-down) to $907/month (MBU-based).
4. **Extensions** (`missions/m6_extensions.py`, tests in `tests/test_extensions.py`):
   - Ext 2 (MBU right-size): $/GB-VRAM table; H100->A100 / MI300X for memory-bound cards, $907/month. Cheapest $/hr is not enough: the card must fit the working set and deliver the needed TB/s at 60% MBU.
   - Ext 3 (`cache_is_worth_it`): with Anthropic's 1.25x write / 0.1x read pricing the break-even is ~0.28 reads for any model (the premium scales with price), vs ~239 reads/prefix in the dataset, so caching is clearly worth it.
   - Ext 4 (reasoning): 8.4% of traffic = 16.5% of $ but 94% of Wh (~170x per query incl. longer outputs). Capping at 3% saves ~60% energy.
   - Ext 5 (carbon): 5 interruptible jobs, 1,789 kWh; us-east-1 -> europe-north1 saves 626 kgCO2 (92%) and $54 electricity. Cheapest/balanced region is us-east-wa; cleanest is europe-north1 (latency trade-off for interactive traffic).
5. **First 3 actions:** (a) move checkpointed training to spot and steady serving to reserved; (b) enforce cascade routing + caching and gate reasoning behind a confidence threshold; (c) alert on MFU/MBU instead of GPU-Util, autoscale idle GPUs to zero, and tag everything to keep coverage >= 80% for chargeback.
