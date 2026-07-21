# Phase A — Screening & Thesis Only (Automated Daily Task)

This is the automated, unattended subset of this pipeline (see `README.md`
for the phase-split overview), run every weekday at 4:30pm Central as a
cloud routine.

It performs **ONLY Steps 1–3** of the playbook. It must **NEVER** perform
Step 4 (re-verify), Step 5 (risk enforcement), Step 6 (dry run / order
review), or Step 7 (`trade_log.jsonl`) — those belong to Phase B. Order-
related tools (`review_equity_order`, `place_equity_order`, and cancel
tools) are hard-blocked at the connector level for this task, but do
not attempt them regardless.

## Step 1 — Build the watchlist

Pull symbols from the Robinhood watchlist named `universe.watchlist_name`
in `risk_rules.json` (read fresh each run — do not assume prior values or
hardcode the name). Call `get_watchlists` to find its `list_id` by
matching `display_name`, then `get_watchlist_items` on that `list_id` —
ignore all other watchlists. Dedupe, filter via `get_equity_fundamentals`
against the **current** values in `risk_rules.json`'s `universe` block,
and cap at `universe.max_candidates_per_cycle`.

**Always add every currently-held position** on top of the watchlist-derived
list: call `get_equity_positions` (account_number from `risk_rules.json`)
and include every symbol found there, **regardless of whether it would
otherwise pass the `universe` filters or is on any watchlist**. A held
position must always stay eligible for a fresh thesis (including a possible
`exit_existing` flag) — it can never be silently dropped for being illiquid,
small-cap, or off a watchlist after the fact. These held-position symbols
don't count against `universe.max_candidates_per_cycle` — add them after
that cap is applied to the watchlist-derived candidates, not before. Log
them with `"stage": "screened", "passed_filters": true, "reason": "currently held — always included"`
regardless of what the fundamentals filters would have said.

(Pulling positions here is just to build the candidate list, not a risk or
stop-loss check — that's still exclusively Phase B's job. See the Hard stop
section below.)

## Step 2 — Gather signals

Pull ~60 days of price history per
surviving candidate via `get_equity_historicals`. Whether a candidate is
worth a news search is a **mechanical check against
`risk_rules.json`'s `signal_thresholds`**, not a judgment call — a
candidate qualifies if it meets **any one** of these three, computed from
data already pulled in Step 1/2 (no extra tool calls needed):

1. **60-day price move**: `abs(latest_close - close_60d_ago) / close_60d_ago >= signal_thresholds.price_move_60d_pct`.
   **"60 days" means 60 *calendar* days, not 60 trading-day bars.**
   `close_60d_ago` must be the `close_price` of the earliest bar returned
   when `get_equity_historicals`'s `start_time` is set to (today's date
   minus 60 calendar days) — i.e. let the tool's own date-scoped query
   determine the bar, don't pull a longer range and count back 60 bars from
   the end. Counting bars instead of dates silently drifts the window
   (60 trading days is ~85-90 calendar days), which understates how recent
   the comparison point actually is and can badly overstate the move.
   If fewer than 60 calendar days of history exist (e.g. a recent IPO),
   compute this over whatever window is actually available and note that in
   the log rather than skipping the check entirely.
2. **Volume spike**: `latest_volume / average_volume_30_days >= signal_thresholds.volume_spike_multiple`
   (both fields come from the `get_equity_fundamentals` call in Step 1).
3. **Near a 52-week extreme**: `(high_52_weeks - current_price) / high_52_weeks <= signal_thresholds.pct_from_52wk_extreme`
   **or** `(current_price - low_52_weeks) / low_52_weeks <= signal_thresholds.pct_from_52wk_extreme`
   (also from Step 1's fundamentals call).

**Log the raw inputs behind every computed ratio, not just the ratio
itself** — see the `signal_check` field format in Output below. A
percentage with no underlying numbers next to it can't be sanity-checked
later without re-pulling the historicals by hand.

If a candidate meets none of these three, it does not get a news search or
a thesis this run — log it as `screened`-only, same as before. Only
candidates that qualify get a search, and stay within
`cadence.news_search_budget_per_cycle` from `risk_rules.json` for the whole
run (not per stock) when spending those searches.

**Exception — currently-held positions always get a fresh thesis**, whether
or not they show a notable signal that day. Run one targeted news search
per held position (in addition to, not counted against, the normal
`cadence.news_search_budget_per_cycle` — this is a small, separate budget
bounded by `max_concurrent_positions`, same pattern as the Monday
weekend-gap searches in Phase B) and produce a Step 3 thesis for it every
run. This is what actually makes `exit_existing` reachable — without a
guaranteed re-check, a slow deterioration with no single sharp signal could
go unnoticed indefinitely.

## Step 3 — Synthesize thesis

For each candidate worth flagging, produce the structured thesis record
shown in `README.md` (symbol, date, thesis, conviction,
invalidation, direction).
- **No price targets.**
- **No forecasting language treated as fact** — frame as "this suggests..."
  not "this will...".

**For a currently-held position specifically**, `direction` should be
either `"long"` (thesis still supports holding it) or `"exit_existing"`
(thesis no longer supports holding it) — never `"avoid"`, which only makes
sense for a candidate not yet held.

**Also include a `pct_below_52wk_high` field** for every `direction: "long"`
candidate: `(high_52_weeks - current_price) / high_52_weeks`, as a decimal
(e.g. `0.15` for 15% below the 52-week high). Both `high_52_weeks` and a
current price are already returned by the `get_equity_fundamentals` call
from Step 1 — no extra tool calls needed, just carry the numbers through.
This exists so Phase B can break ties between same-conviction candidates
when more qualify than there's room for (see `PHASE_B_TASK.md` Step 5) — it
is a disclosed proxy for "room in the setup," **not** a rigorous fair-value
calculation. Omit this field for `avoid`/`exit_existing` candidates; it
isn't used for those.

## Output

**Overwrite `pending_proposals.jsonl` in this folder at the start of this
run** — clear/replace its previous contents rather than appending to them.
Proposals are only good for the next open, not indefinitely, so this file
should hold exactly one run's worth of candidates at a time (today's).
Historical proposals remain auditable through `trade_log.jsonl`, which
Phase B writes to when it acts on a proposal — this file is just the
day's working handoff, not a permanent log.

Every line (both stages below) must include a `"timestamp"` field — the
actual real-world time this run started (e.g. via
`TZ='America/Chicago' date +'%H:%M:%S'`), never guessed or hallucinated —
in addition to the plain `"date"` field.
`timestamp` is time-of-day only (`HH:mm:ss`, e.g. `"16:30:02"`) — do not
prepend or duplicate the date into it. This is purely for human readability
when scanning the log (e.g. distinguishing same-day re-runs); it does not
replace `"date"` and must never be used for idempotency or any other logic
anywhere in this pipeline.

Write:
- One `"stage": "screened"` line per candidate evaluated (`passed_filters`
  true/false, `avg_volume`, `market_cap`, `reason` if rejected) — same shape
  as the `screened` stage in `trade_log_template.jsonl`, plus a
  `"signal_check"` field noting which Step 2 threshold(s) triggered, **each
  ratio paired with the raw prices/volumes used to compute it** (not just
  the resulting percentage) so the arithmetic itself is checkable straight
  from the log:
  - `"price_move_60d: 0.2925 (close_60d_ago: 424.10 -> latest_close: 548.13)"`
  - `"volume_spike: 2.3x (latest_volume: 68000000 / avg_volume_30d: 29421634)"`
  - `"near_52wk_high: 0.02 (current_price: 314.86 / high_52_weeks: 321.00)"`
  - `"none"` if it didn't qualify for a thesis this run — no raw values
    needed in that case.
  This is what makes the Step 2 gate auditable later instead of just
  trusted — the raw numbers must be the actual values pulled from
  `get_equity_historicals`/`get_equity_fundamentals` this run, never
  back-computed to match a percentage decided some other way.
- One `"stage": "thesis"` line per candidate worth flagging — same shape as
  the `thesis` stage in `trade_log_template.jsonl`, plus the
  `pct_below_52wk_high` field described in Step 3 for `long` candidates.

Do not touch `trade_log.jsonl` — that file is reserved for full cycles that
include risk enforcement and order review/execution (Steps 4–7), run
separately by reading `pending_proposals.jsonl`.

**After all `screened` and `thesis` lines, append one `"stage": "summary"`
line per decision bucket** (still to `pending_proposals.jsonl`, still
overwritten fresh each run) — just a plain symbol list per bucket, so the
whole run's outcome is scannable at a glance. Phase B only reads
`"stage": "thesis"` entries (Step 0.3), so these are inert to it — purely
for human readability. One line per bucket, in this order, always all five
even if empty (`"symbols": []`):

```json
{"date": "YYYY-MM-DD", "timestamp": "HH:mm:ss", "stage": "summary", "decision": "rejected", "symbols": ["AMC", "ADDYY"]}
{"date": "YYYY-MM-DD", "timestamp": "HH:mm:ss", "stage": "summary", "decision": "no_signal", "symbols": ["TSLA", "NVDA", "..."]}
{"date": "YYYY-MM-DD", "timestamp": "HH:mm:ss", "stage": "summary", "decision": "avoid", "symbols": ["SPCX", "LCID", "..."]}
{"date": "YYYY-MM-DD", "timestamp": "HH:mm:ss", "stage": "summary", "decision": "long", "symbols": ["AAPL", "AMD", "..."]}
{"date": "YYYY-MM-DD", "timestamp": "HH:mm:ss", "stage": "summary", "decision": "exit_existing", "symbols": []}
```

`rejected` = failed a universe filter. `no_signal` = passed filters but no
Step 2 signal, no thesis. `avoid`/`long`/`exit_existing` = matches that
candidate's `thesis` line `direction`. No reason or conviction fields —
just the symbol lists.

## Hard stop

Do not call `review_equity_order`, `place_equity_order`, or any cancel/order
tool. Do not check or reference `execution.mode`. `get_equity_positions` may
only be used to build the candidate list per Step 1 above — do not compute
or reference stop-loss drawdown here; that check, and all other risk
enforcement, is exclusively Phase B's job.
