#!/usr/bin/env python3
# Part of FriesTrader (https://github.com/YizhiSong/FriesTrader)
# Copyright (c) 2026 Yizhi Song, MIT License -- see LICENSE
"""Sort Step 5's merged new+held candidate list into priority order, per
risk_rules.json/PHASE_B_TASK.md's "Candidate priority order":
  1. conviction tier: high before medium before low
  2. within a tier, risk_flags count ascending (missing field = worst
     case, sorted last)
  3. still tied, pct_below_52wk_high descending (missing field = lowest
     priority in its tier)

Reads a JSON array of candidate objects from stdin (each at least
{"symbol": ..., "conviction": ...}, optionally "risk_flags": [...] and
"pct_below_52wk_high": <float>) and writes the same objects, reordered,
to stdout — a stable sort, so any other tie is left in input order.
Output is meant to be piped straight into position_sizing.py's stdin.
"""
import json
import sys

CONVICTION_RANK = {"high": 0, "medium": 1, "low": 2}
MISSING_RISK_FLAGS_SENTINEL = 10 ** 9
MISSING_PCT_SENTINEL = -1.0  # safe: pct_below_52wk_high is always >= 0


def sort_key(candidate):
    conviction_rank = CONVICTION_RANK[candidate["conviction"]]

    if "risk_flags" in candidate and candidate["risk_flags"] is not None:
        risk_flags_count = len(candidate["risk_flags"])
    else:
        risk_flags_count = MISSING_RISK_FLAGS_SENTINEL

    pct = candidate.get("pct_below_52wk_high")
    pct = pct if pct is not None else MISSING_PCT_SENTINEL

    return (conviction_rank, risk_flags_count, -pct)


def main():
    try:
        candidates = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid candidates JSON on stdin: {e}"}), file=sys.stderr)
        sys.exit(1)

    for c in candidates:
        if c.get("conviction") not in CONVICTION_RANK:
            print(json.dumps({"error": f"symbol {c.get('symbol')!r} has missing/invalid "
                                        f"conviction {c.get('conviction')!r}"}), file=sys.stderr)
            sys.exit(1)

    candidates.sort(key=sort_key)
    print(json.dumps(candidates))


if __name__ == "__main__":
    main()
