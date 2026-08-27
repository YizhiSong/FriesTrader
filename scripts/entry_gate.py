#!/usr/bin/env python3
"""Single buy/top-up gate for one candidate, combining every independent
per-symbol condition that can block a buy: entry_price_gap, entry_extension,
wash_sale_avoidance (buy-side guard), and the sell re-entry lock. Does NOT
cover position_sizing.py's slot/cash allocation, which is a joint decision
across all of a cycle's candidates, not a per-symbol one.
"""
import argparse
import datetime
import json
import sys


def parse_date(s):
    return datetime.date.fromisoformat(s)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fresh-ask", type=float, required=True)

    p.add_argument("--thesis-price", type=float, required=True)
    p.add_argument("--entry-price-gap-max-pct", type=float, required=True)

    p.add_argument("--daily-closes", required=True,
                    help="comma-separated closes for the extension moving average")
    p.add_argument("--max-extension-pct", type=float, required=True)

    p.add_argument("--wash-sale-enabled", action="store_true")
    p.add_argument("--wash-sale-lookback-days", type=int, default=0)
    p.add_argument("--loss-sale-dates", default="",
                    help="comma-separated ISO dates of this symbol's realized-loss "
                         "closing sales across all linked accounts")
    p.add_argument("--today", required=True, type=parse_date)

    p.add_argument("--last-sell-reason",
                    choices=["stop_loss", "take_profit", "conviction_trim", "exit_existing"])
    p.add_argument("--last-sell-price", type=float)
    p.add_argument("--last-sell-date", type=parse_date)
    p.add_argument("--last-sell-was-gain", choices=["true", "false"],
                    help="required with --last-sell-reason: did that sale close above "
                         "average cost (gain -- time-bound lock) or below it (loss -- "
                         "indefinite lock)?")
    p.add_argument("--reentry-lock-max-trading-days", type=int)
    p.add_argument("--trading-days-since-sell", type=int)

    args = p.parse_args()
    result = {}

    gap_pct = (args.fresh_ask - args.thesis_price) / args.thesis_price
    result["entry_price_gap"] = {
        "gap_pct": round(gap_pct, 6),
        "blocked": gap_pct > args.entry_price_gap_max_pct,
    }

    closes = [float(c) for c in args.daily_closes.split(",") if c.strip()]
    if not closes:
        print(json.dumps({"error": "daily-closes must be non-empty"}), file=sys.stderr)
        sys.exit(1)
    moving_avg = sum(closes) / len(closes)
    extension_pct = (args.fresh_ask - moving_avg) / moving_avg
    result["entry_extension"] = {
        "moving_avg": round(moving_avg, 4),
        "extension_pct": round(extension_pct, 6),
        "blocked": extension_pct > args.max_extension_pct,
    }

    wash_sale_blocked = False
    matching_loss_sale_date = None
    if args.wash_sale_enabled:
        for d in (parse_date(s) for s in args.loss_sale_dates.split(",") if s.strip()):
            if (args.today - d).days <= args.wash_sale_lookback_days:
                wash_sale_blocked = True
                matching_loss_sale_date = d.isoformat()
                break
    result["wash_sale_avoidance"] = {
        "enabled": args.wash_sale_enabled,
        "blocked": wash_sale_blocked,
        "matching_loss_sale_date": matching_loss_sale_date,
    }

    reentry_locked = False
    reentry_detail = None
    if args.last_sell_reason:
        if args.last_sell_price is None or args.last_sell_date is None:
            print(json.dumps({"error": "--last-sell-price and --last-sell-date required "
                                        "with --last-sell-reason"}), file=sys.stderr)
            sys.exit(1)
        if args.last_sell_was_gain is None:
            print(json.dumps({"error": "--last-sell-was-gain required with "
                                        "--last-sell-reason"}), file=sys.stderr)
            sys.exit(1)
        price_locked = args.fresh_ask > args.last_sell_price
        if args.last_sell_was_gain == "false":
            reentry_locked = price_locked
        else:
            if args.reentry_lock_max_trading_days is None or args.trading_days_since_sell is None:
                print(json.dumps({"error": "--reentry-lock-max-trading-days and "
                                            "--trading-days-since-sell required for a "
                                            "gain-closed sell"}), file=sys.stderr)
                sys.exit(1)
            time_cleared = args.trading_days_since_sell >= args.reentry_lock_max_trading_days
            reentry_locked = price_locked and not time_cleared
        reentry_detail = {
            "reason": args.last_sell_reason,
            "sell_price": args.last_sell_price,
            "sell_date": args.last_sell_date.isoformat(),
            "last_sell_was_gain": args.last_sell_was_gain,
            "price_condition_locked": price_locked,
            "trading_days_since_sell": args.trading_days_since_sell,
            "reentry_lock_max_trading_days": args.reentry_lock_max_trading_days,
        }
    result["sell_reentry_lock"] = {"blocked": reentry_locked, "detail": reentry_detail}

    blocking = [name for name in
                ("entry_price_gap", "entry_extension", "wash_sale_avoidance", "sell_reentry_lock")
                if result[name]["blocked"]]
    result["passed"] = len(blocking) == 0
    result["blocking_conditions"] = blocking
    result["action"] = "buy_ok" if result["passed"] else "skip_buy"

    print(json.dumps(result))


if __name__ == "__main__":
    main()
