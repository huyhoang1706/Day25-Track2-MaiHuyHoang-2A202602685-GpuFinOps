# NimbusAI — GPU Cost Optimization Report

**Period:** monthly  
**Baseline spend:** $27,622  
**Optimized spend:** $14,697  
**Projected savings:** $12,925  (**47%**)

## Savings by lever

| Lever | Savings (USD) |
|---|---|
| Inference (cascade/cache/batch) | $1,630 |
| Purchasing (spot/reserved) | $10,040 |
| Right-size util-lies | $655 |
| Kill idle GPUs | $600 |

## Sustainability

- Energy per query: 0.24 Wh
- Carbon per query: 0.091 gCO2e
- Cleanest region: europe-north1

## Unit economics

- Inference: **$8.651 -> $1.436 per 1M tokens** (83.4% lower) from cascade + prompt cache + batch.
- Purchasing: on-demand $25,667 -> $15,627/month (39.1%).
- Blended: $27,622 -> $14,697/month (46.8%).

## Why GPU-Util lies

Flagged: `gpu-h100-4` (H100, util 98.2%, MFU 19%), `gpu-a10g-1` (A10G, util 96.9%, MFU 27%).

`nvidia-smi` GPU-Util only records the share of sample periods in which *at least one kernel* was resident - it says nothing about how many SMs were busy or how many FLOPs retired. A GPU stalled on HBM reads (memory-bound decode, small batch), waiting on host/PCIe/NCCL, or running tiny kernels still shows ~100%. MFU (achieved/peak FLOPs) and MBU expose it. Financially, you pay the full $/hr for a card delivering a fraction of its useful work, so effective $/token is several times the sticker price; the fix is batching/right-sizing, not buying more GPUs. Right-sizing these cards saves $655/month; a bandwidth-based check (MBU, target 60%) finds $907/month using cheaper cards that still fit the working set.

## Prioritised actions

| # | Action | Monthly impact | Why this order |
|---|---|---|---|
| 1 | Purchasing (spot/reserved) | $10,040 (78% of savings) | largest $, no code change; checkpoint training jobs first |
| 2 | Inference (cascade/cache/batch) | $1,630 (13% of savings) | software-only, quality-gated; scales with traffic |
| 3 | Right-size util-lies | $655 (5% of savings) | needs benchmarking per model, moderate risk |
| 4 | Kill idle GPUs | $600 (5% of savings) | trivial to do (autoscale-to-zero) but small $ |

## Sustainability notes

- Reasoning queries are 8.4% of traffic but 16.8% of spend and **94.0% of energy** (148.2 Wh vs 0.858 Wh per query, ~80x). Capping them at 3% (route only low-confidence/hard tasks) cuts energy 59.6%.
- Moving the 5 interruptible jobs (1,789 kWh) from us-east-1 to `europe-north1` removes 626.1 kgCO2 (92.1%); electricity is $214.68 -> $161.01. Cheapest is `us-east-wa`, best cost/carbon balance `us-east-wa`; the cleanest region is far from US users, so use it only for latency-insensitive training/batch, not interactive serving.
- Prompt caching pays off immediately (avg 239.0 reads/prefix vs break-even 0.28 on the small tier, 0.28 on the large tier).

_Figures are June-2026 as-of snapshots; re-baseline before acting._