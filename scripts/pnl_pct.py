#!/usr/bin/env python3
# Part of FriesTrader (https://github.com/YizhiSong/FriesTrader)
# Copyright (c) 2026 Yizhi Song, MIT License -- see LICENSE
"""Compute daily/weekly P&L % against starting_capital_usd and the loss-limit halt decision.

Per risk_rules.json/PHASE_B_TASK.md spec: daily_pnl_pct and weekly_pnl_pct
are always denominated against starting_capital_usd, never against
get_realized_pnl's own total_rate_of_return (which is denominated against
capital in the closed trades, a different and smaller base).
"""
import argparse
import json
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--daily-realized-usd", type=float, required=True,
                    help="get_realized_pnl span=day total_returns (0 if no trades today)")
    p.add_argument("--weekly-realized-usd", type=float, required=True,
                    help="get_realized_pnl span=week total_returns (0 if no trades this week)")
    p.add_argument("--starting-capital-usd", type=float, required=True,
                    help="risk_rules.json starting_capital_usd")
    p.add_argument("--daily-limit-pct", type=float, required=True,
                    help="risk_rules.json loss_limits.daily_loss_limit_pct_of_account")
    p.add_argument("--weekly-limit-pct", type=float, required=True,
                    help="risk_rules.json loss_limits.weekly_loss_limit_pct_of_account")
    args = p.parse_args()

    if args.starting_capital_usd <= 0:
        print(json.dumps({"error": "starting_capital_usd must be positive"}), file=sys.stderr)
        sys.exit(1)

    daily_pnl_pct = args.daily_realized_usd / args.starting_capital_usd
    weekly_pnl_pct = args.weekly_realized_usd / args.starting_capital_usd

    daily_breach = (-daily_pnl_pct) >= args.daily_limit_pct
    weekly_breach = (-weekly_pnl_pct) >= args.weekly_limit_pct

    reasons = []
    if daily_breach:
        reasons.append(f"daily drawdown {(-daily_pnl_pct):.4%} >= daily limit {args.daily_limit_pct:.4%}")
    if weekly_breach:
        reasons.append(f"weekly drawdown {(-weekly_pnl_pct):.4%} >= weekly limit {args.weekly_limit_pct:.4%}")

    print(json.dumps({
        "daily_pnl_pct": round(daily_pnl_pct, 6),
        "weekly_pnl_pct": round(weekly_pnl_pct, 6),
        "entries_halted": daily_breach or weekly_breach,
        "halt_reason": "; ".join(reasons) if reasons else None,
    }))


if __name__ == "__main__":
    main()
