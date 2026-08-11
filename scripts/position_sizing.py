#!/usr/bin/env python3
"""Run Step 5's per-candidate position-sizing/risk_check math over an
already priority-sorted candidate list, per risk_rules.json/
PHASE_B_TASK.md.

Candidates are processed in the order given (the caller must have
already applied the conviction/risk_flags/pct_below_52wk_high sort) so
that cash_remaining and concurrent_positions_after compound correctly
across the list, the same way a human working top-to-bottom down a
priority list would.

Candidate JSON is read from stdin, an array of objects:
  {"symbol": "GTLB", "group": "new", "conviction": "high"}
  {"symbol": "AMZN", "group": "held", "conviction": "high", "current_position_value": 100.39}
`group` is "new" or "held". `current_position_value` is required for
"held" candidates only.
"""
import argparse
import json
import sys


def parse_pct_map(s):
    out = {}
    for part in s.split(","):
        tier, pct = part.split(":")
        out[tier] = float(pct)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--total-value", type=float, required=True)
    p.add_argument("--cash-start", type=float, required=True,
                    help="live cash balance (get_portfolio.cash) at the start of Step 5")
    p.add_argument("--concurrent-positions-start", type=int, required=True,
                    help="live open position count at the start of Step 5, after any sells this cycle resolved")
    p.add_argument("--entries-halted", action="store_true",
                    help="set if the loss-limit check (or a stop-loss/take-profit script failure) halted entries this cycle")
    p.add_argument("--max-position-pct", type=float, required=True,
                    help="risk_rules.json position_sizing.max_position_pct_of_account")
    p.add_argument("--max-concurrent-positions", type=int, required=True)
    p.add_argument("--min-cash-buffer-pct", type=float, required=True)
    p.add_argument("--min-top-up-usd", type=float, required=True)
    p.add_argument("--min-top-up-pct-of-target", type=float, required=True)
    p.add_argument("--conviction-pct", type=parse_pct_map, required=True,
                    help='fixed conviction-tier sizing table, e.g. "high:0.20,medium:0.12,low:0.06"')
    args = p.parse_args()

    try:
        candidates = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid candidates JSON on stdin: {e}"}), file=sys.stderr)
        sys.exit(1)

    cash_remaining = args.cash_start
    concurrent_positions_after = args.concurrent_positions_start
    min_cash_buffer = args.min_cash_buffer_pct * args.total_value

    results = []

    for c in candidates:
        symbol = c["symbol"]
        group = c["group"]
        conviction = c["conviction"]
        conviction_pct = args.conviction_pct[conviction]

        if args.entries_halted:
            halted_result = {
                "symbol": symbol, "group": group, "conviction": conviction,
                "passed": False,
                "reason": "loss limit halt — daily/weekly drawdown breached",
            }
            if group == "held":
                halted_result["position_action"] = "top_up"
            results.append(halted_result)
            continue

        if group == "new":
            dollar_amount = round(conviction_pct * args.total_value, 2)
            candidate_concurrent_after = concurrent_positions_after + 1
            candidate_cash_after = cash_remaining - dollar_amount

            reasons = []
            if conviction_pct > args.max_position_pct:
                reasons.append(f"position size {conviction_pct:.4%} exceeds max_position_pct_of_account {args.max_position_pct:.4%}")
            if candidate_concurrent_after > args.max_concurrent_positions:
                reasons.append(
                    f"concurrent_positions_after ({candidate_concurrent_after}) exceeds "
                    f"max_concurrent_positions ({args.max_concurrent_positions}) — cap filled by "
                    f"higher-priority candidates this cycle"
                )
            if candidate_cash_after < min_cash_buffer:
                reasons.append(
                    f"cash_remaining after trade (${candidate_cash_after:.2f}) would fall below "
                    f"min_cash_buffer_pct (${min_cash_buffer:.2f}) of total_value"
                )

            if reasons:
                results.append({
                    "symbol": symbol, "group": group, "conviction": conviction,
                    "passed": False, "reason": "; ".join(reasons),
                    "dollar_amount": dollar_amount,
                })
                continue

            cash_remaining = candidate_cash_after
            concurrent_positions_after = candidate_concurrent_after
            results.append({
                "symbol": symbol, "group": group, "conviction": conviction,
                "passed": True,
                "dollar_amount": dollar_amount,
                "concurrent_positions_after": concurrent_positions_after,
                "cash_remaining_after": round(cash_remaining, 2),
                "cash_buffer_after_pct": round(cash_remaining / args.total_value, 6),
            })

        elif group == "held":
            current_position_value = c["current_position_value"]
            target_size = conviction_pct * args.total_value
            headroom = target_size - current_position_value

            if headroom <= 0:
                results.append({
                    "symbol": symbol, "group": group, "conviction": conviction,
                    "passed": False, "position_action": "top_up",
                    "reason": "already at or above target size for its conviction tier — no top-up",
                    "current_position_value": round(current_position_value, 2),
                    "target_size": round(target_size, 2),
                    "headroom": round(headroom, 2),
                })
                continue

            ceiling_room = args.max_position_pct * args.total_value - current_position_value
            top_up_amount = round(min(headroom, ceiling_room), 2)
            min_top_up_threshold = round(max(1.00, args.min_top_up_usd,
                                              args.min_top_up_pct_of_target * target_size), 2)

            if top_up_amount < min_top_up_threshold:
                results.append({
                    "symbol": symbol, "group": group, "conviction": conviction,
                    "passed": False, "position_action": "top_up",
                    "reason": (
                        f"top-up amount ${top_up_amount:.2f} is below the min top-up threshold "
                        f"${min_top_up_threshold:.2f} (broker $1.00 minimum vs. min_top_up_usd "
                        f"${args.min_top_up_usd:.2f} vs. min_top_up_pct_of_target "
                        f"{args.min_top_up_pct_of_target:.0%} of target ${target_size:.2f}, "
                        f"whichever is highest) — no order attempted"
                    ),
                    "current_position_value": round(current_position_value, 2),
                    "target_size": round(target_size, 2),
                    "headroom": round(headroom, 2),
                })
                continue

            candidate_cash_after = cash_remaining - top_up_amount
            if candidate_cash_after < min_cash_buffer:
                results.append({
                    "symbol": symbol, "group": group, "conviction": conviction,
                    "passed": False, "position_action": "top_up",
                    "reason": (
                        f"cash_remaining after trade (${candidate_cash_after:.2f}) would fall below "
                        f"min_cash_buffer_pct (${min_cash_buffer:.2f}) of total_value"
                    ),
                    "current_position_value": round(current_position_value, 2),
                    "target_size": round(target_size, 2),
                    "headroom": round(headroom, 2),
                })
                continue

            cash_remaining = candidate_cash_after
            results.append({
                "symbol": symbol, "group": group, "conviction": conviction,
                "passed": True, "position_action": "top_up",
                "current_position_value": round(current_position_value, 2),
                "target_size": round(target_size, 2),
                "headroom": round(headroom, 2),
                "dollar_amount": top_up_amount,
                "cash_remaining_after": round(cash_remaining, 2),
                "cash_buffer_after_pct": round(cash_remaining / args.total_value, 6),
            })

        else:
            print(json.dumps({"error": f"unknown group '{group}' for symbol {symbol}"}), file=sys.stderr)
            sys.exit(1)

    print(json.dumps({
        "results": results,
        "cash_remaining_final": round(cash_remaining, 2),
        "concurrent_positions_after_final": concurrent_positions_after,
    }))


if __name__ == "__main__":
    main()
