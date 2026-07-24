# Phase A — Screening & Thesis Only (Automated Daily Task)

Automated subset of this pipeline (see `README.md`), run every weekday
4:30pm Central as a cloud routine.

Performs **ONLY Steps 1–3**. **NEVER** Step 4 (re-verify), 5 (risk
enforcement), 6 (dry run/order review), or 7 (`trade_log.jsonl`) — those
belong to Phase B. Order tools (`review_equity_order`,
`place_equity_order`, cancel) are hard-blocked at the connector level; do
not attempt them anyway.

## Step 1 — Build the watchlist

Pull symbols from the Robinhood watchlist named `universe.watchlist_name`
in `risk_rules.json` (read fresh each run — don't assume prior values or
hardcode the name). Call `get_watchlists` to find its `list_id` by
matching `display_name`, then `get_watchlist_items` on that `list_id` —
ignore all other watchlists. Dedupe, filter via `get_equity_fundamentals`
against `risk_rules.json`'s current `universe` block, and cap at
`universe.max_candidates_per_cycle`.

`exclude`'s `"penny_stocks"` entry is a **mechanical price check, not a
judgment call**: exclude a candidate if its current price <
`universe.penny_stock_price_threshold_usd`, full stop — same cutoff every
run, regardless of how the stock is otherwise trading. Log the reason as
`"penny stock (price $<X>, under $<threshold>) — excluded per universe.exclude: penny_stocks"`.

**Always add every held position** on top of the watchlist-derived list
(`get_equity_positions`, account_number from `risk_rules.json`),
regardless of filters or watchlist membership — a held position must
stay eligible for a fresh thesis (including `exit_existing`) and never
get silently dropped for being illiquid, small-cap, or off the list.
Held positions don't count against `max_candidates_per_cycle` (add them
after that cap is applied). Log as
`"stage": "screened", "passed_filters": true, "reason": "currently held — always included"`
regardless of what the filters would have said.

(This just builds the candidate list — not a risk/stop-loss check; that's
Phase B's job. See Hard stop below.)

## Step 2 — Gather signals

Pull ~60 days of price history per candidate (`get_equity_historicals`),
called fresh for every candidate this run. Never reuse `close_60d_ago`,
`latest_close`, or any other historicals-derived value from a prior
run's `pending_proposals.jsonl` or `trade_log.jsonl`, even if today's
figure looks unchanged from yesterday's — every number in `signal_check`
must come from this run's own tool call. Whether it's worth a news
search is mechanical, against `risk_rules.json`'s `signal_thresholds` —
qualifies if it meets **any one** of these three (no extra tool calls
needed):

1. **60-day price move**: `abs(latest_close - close_60d_ago) / close_60d_ago >= signal_thresholds.price_move_60d_pct`.
   **"60 days" = 60 *calendar* days, not trading bars.** Get
   `close_60d_ago` as the earliest bar's `close_price` when
   `get_equity_historicals`'s `start_time` = today minus 60 calendar days
   — don't pull a longer range and count back 60 bars (that drifts to
   ~85-90 calendar days and overstates the move). If less than 60 days of
   history exists (e.g. recent IPO), compute over the available window
   and note it rather than skipping.
2. **Volume spike**: `latest_volume / average_volume_30_days >= signal_thresholds.volume_spike_multiple`
   (both from Step 1's `get_equity_fundamentals` call).
3. **Near a 52-week extreme**: `(high_52_weeks - current_price) / high_52_weeks <= signal_thresholds.pct_from_52wk_extreme`
   **or** `(current_price - low_52_weeks) / low_52_weeks <= signal_thresholds.pct_from_52wk_extreme`
   (also from Step 1's fundamentals call).

**Log the raw inputs behind every ratio, not just the ratio** (see
`signal_check` format below) — otherwise it can't be sanity-checked
without re-pulling data.

If none apply, no search/thesis this run — log as `screened`-only.
Qualifying candidates' searches stay within
`cadence.news_search_budget_per_cycle` (per run, not per stock).

**Exception — held positions always get a fresh thesis**, signal or not.
Run one targeted news search per held position (separate budget from
`cadence.news_search_budget_per_cycle`, bounded by
`max_concurrent_positions`, same pattern as Phase B's Monday weekend-gap
searches) and produce a thesis every run — this is what makes
`exit_existing` reachable, since a slow deterioration with no sharp
signal would otherwise go unnoticed.

## Step 3 — Synthesize thesis

For each flagged candidate, produce the thesis record from `README.md`
(symbol, date, thesis, conviction, invalidation, direction).
- **No price targets.**
- **No forecasting as fact** — "this suggests..." not "this will...".

**For a held position**, `direction` is `"long"` (still supports holding)
or `"exit_existing"` (no longer does) — never `"avoid"` (that's only for
not-yet-held candidates).

**Include `pct_below_52wk_high`** for every `direction: "long"` candidate:
`(high_52_weeks - current_price) / high_52_weeks` (e.g. `0.15`). Both
values already come from Step 1's `get_equity_fundamentals` call. Used by
Phase B (Step 5) to break ties between same-conviction candidates — a
disclosed "room in the setup" proxy, not a rigorous fair-value
calculation. Omit for `avoid`/`exit_existing` candidates; it isn't used
for those.

**Include a `sources` field** listing outlet name + URL for every search
result that informed this thesis (e.g.
`["Reuters: https://...", "Company Q2 press release: https://..."]`) —
this is what makes the reasoning step auditable later instead of just
trusted. Prefer primary sources (company filings/press releases, wire
services like Reuters/AP) and major outlets (Bloomberg, WSJ, CNBC, etc.)
over aggregator/content-farm sites when both turn up in the same search;
if only a lower-tier source is available, use it and cite it rather than
omitting the field.

## Output

**Overwrite `pending_proposals.jsonl` at the start of this run** (replace,
don't append) — it should hold only today's candidates; history remains
auditable via `trade_log.jsonl`, which Phase B writes to when acting on a
proposal.

Every line needs a real `"timestamp"` (`HH:mm:ss`, e.g. via
`TZ='America/Chicago' date +'%H:%M:%S'` — never guessed) alongside
`"date"`. Time-of-day only, no date prefix. For human readability only —
never used for idempotency or other logic.

Write:
- One `"stage": "screened"` line per candidate (`passed_filters`,
  `avg_volume`, `market_cap`, `reason` if rejected — shape matches
  `trade_log_template.jsonl`), plus `"signal_check"` noting which Step 2
  threshold(s) triggered, **each ratio paired with its raw inputs**
  (examples below) so the arithmetic is checkable — raw numbers must be
  this run's actual pulled values, never back-computed to fit a
  percentage:
  - `"price_move_60d: 0.2925 (close_60d_ago: 424.10 -> latest_close: 548.13)"`
  - `"volume_spike: 2.3x (latest_volume: 68000000 / avg_volume_30d: 29421634)"`
  - `"near_52wk_high: 0.02 (current_price: 314.86 / high_52_weeks: 321.00)"`
  - `"none"` if it didn't qualify for a thesis this run — no raw values
    needed in that case.
- One `"stage": "thesis"` line per flagged candidate (shape matches
  `trade_log_template.jsonl`), plus `pct_below_52wk_high` for `long`
  candidates (Step 3).

Do not touch `trade_log.jsonl` — reserved for Steps 4–7 (Phase B), which
reads `pending_proposals.jsonl` separately.

**After all `screened`/`thesis` lines, append one `"stage": "summary"`
line per decision bucket** (to `pending_proposals.jsonl`) — a plain
symbol list per bucket for at-a-glance readability. Phase B only reads
`"stage": "thesis"` entries, so these are inert to it. Always all five
buckets, in order, even if empty:

```json
{"date": "YYYY-MM-DD", "timestamp": "HH:mm:ss", "stage": "summary", "decision": "rejected", "symbols": ["AMC", "ADDYY"]}
{"date": "YYYY-MM-DD", "timestamp": "HH:mm:ss", "stage": "summary", "decision": "no_signal", "symbols": ["TSLA", "NVDA", "..."]}
{"date": "YYYY-MM-DD", "timestamp": "HH:mm:ss", "stage": "summary", "decision": "avoid", "symbols": ["SPCX", "LCID", "..."]}
{"date": "YYYY-MM-DD", "timestamp": "HH:mm:ss", "stage": "summary", "decision": "long", "symbols": ["AAPL", "AMD", "..."]}
{"date": "YYYY-MM-DD", "timestamp": "HH:mm:ss", "stage": "summary", "decision": "exit_existing", "symbols": []}
```

`rejected` = failed universe filter. `no_signal` = passed filters, no
Step 2 signal/thesis. `avoid`/`long`/`exit_existing` = matches the
thesis's `direction`. No reason/conviction fields — symbol lists only.

## Hard stop

Do not call `review_equity_order`, `place_equity_order`, or any
cancel/order tool, or check `execution.mode`. `get_equity_positions` is
for Step 1's candidate list only — no stop-loss/drawdown computation
here; all risk enforcement is Phase B's job.
