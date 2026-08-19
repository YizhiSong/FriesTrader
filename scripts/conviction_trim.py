#!/usr/bin/env python3
"""Compute conviction-trim firing for one held position, per
risk_rules.json/PHASE_B_TASK.md Step 5.
"""
import argparse
import json
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--conviction", required=True, choices=["low", "medium", "high"])
    p.add_argument("--current-position-value", type=float, required=True)
    p.add_argument("--target-size", type=float, required=True)
    p.add_argument("--overweight-trigger-pct", type=float, required=True)
    p.add_argument("--prior-consecutive-low-overweight-cycles", type=int, required=True,
                    help="count of consecutive prior risk_check cycles (most recent first, not "
                         "including this one) where conviction was 'low' AND current_position_value "
                         "exceeded target_size by more than overweight_trigger_pct, from a "
                         "trade_log.jsonl lookup")
    p.add_argument("--min-low-conviction-cycles", type=int, required=True)
    args = p.parse_args()

    if args.target_size <= 0:
        print(json.dumps({"error": "target_size must be positive"}), file=sys.stderr)
        sys.exit(1)

    overweight_pct = (args.current_position_value - args.target_size) / args.target_size
    is_overweight = overweight_pct > args.overweight_trigger_pct
    qualifies_this_cycle = args.conviction == "low" and is_overweight
    consecutive_cycles = (args.prior_consecutive_low_overweight_cycles + 1) if qualifies_this_cycle else 0
    triggered = qualifies_this_cycle and consecutive_cycles >= args.min_low_conviction_cycles
    trim_dollar_amount = round(args.current_position_value - args.target_size, 2) if triggered else None

    print(json.dumps({
        "overweight_pct": round(overweight_pct, 6),
        "qualifies_this_cycle": qualifies_this_cycle,
        "consecutive_cycles": consecutive_cycles,
        "triggered": triggered,
        "trim_dollar_amount": trim_dollar_amount,
        "action": "sell_partial_position" if triggered else "hold_monitor",
    }))


if __name__ == "__main__":
    main()
