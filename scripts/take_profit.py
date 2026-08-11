#!/usr/bin/env python3
"""Compute tiered take-profit firing for one position, per
risk_rules.json/PHASE_B_TASK.md Step 5.

Tiers are evaluated in ascending gain_pct order; a tier already fired
this holding period is skipped, and a tier whose gain_pct is now met is
fired against the position's quantity as it stands after any earlier
tier fired in this same cycle has already reduced it.
"""
import argparse
import json
import sys


def parse_tiers(s):
    tiers = []
    for part in s.split(","):
        gain_pct_str, sell_fraction_str = part.split(":")
        tiers.append({"gain_pct": float(gain_pct_str), "sell_fraction": float(sell_fraction_str)})
    tiers.sort(key=lambda t: t["gain_pct"])
    return tiers


def parse_float_set(s):
    if not s:
        return set()
    return {float(x) for x in s.split(",")}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--average-cost", type=float, required=True)
    p.add_argument("--current-price", type=float, required=True)
    p.add_argument("--quantity", type=float, required=True,
                    help="position quantity as it stands right now, before this cycle's tiers are evaluated")
    p.add_argument("--tiers", type=parse_tiers, required=True,
                    help="risk_rules.json take_profit.tiers as gain_pct:sell_fraction pairs, "
                         "e.g. '0.15:0.25,0.30:0.25,0.50:0.25'")
    p.add_argument("--already-fired", type=parse_float_set, default=set(),
                    help="comma-separated gain_pct values already fired this holding period "
                         "(from a trade_log.jsonl lookup), e.g. '0.15'")
    args = p.parse_args()

    if args.average_cost <= 0:
        print(json.dumps({"error": "average_cost must be positive"}), file=sys.stderr)
        sys.exit(1)

    gain_pct = (args.current_price - args.average_cost) / args.average_cost

    tiers_status = []
    fired_this_cycle = []
    running_qty = args.quantity

    for tier in args.tiers:
        already_fired = any(abs(tier["gain_pct"] - f) < 1e-9 for f in args.already_fired)
        if already_fired:
            tiers_status.append({"gain_pct": tier["gain_pct"], "fired": True})
            continue
        if gain_pct >= tier["gain_pct"]:
            quantity_before = running_qty
            quantity_sold = quantity_before * tier["sell_fraction"]
            running_qty -= quantity_sold
            fired_this_cycle.append({
                "tier_gain_pct": tier["gain_pct"],
                "sell_fraction": tier["sell_fraction"],
                "quantity_before": round(quantity_before, 6),
                "quantity_sold": round(quantity_sold, 6),
            })
            tiers_status.append({"gain_pct": tier["gain_pct"], "fired": True})
        else:
            tiers_status.append({"gain_pct": tier["gain_pct"], "fired": False})

    triggered = len(fired_this_cycle) > 0

    print(json.dumps({
        "gain_pct": round(gain_pct, 6),
        "tiers_status": tiers_status,
        "fired_this_cycle": fired_this_cycle,
        "triggered": triggered,
        "action": "sell_partial_position" if triggered else "hold_monitor",
    }))


if __name__ == "__main__":
    main()
