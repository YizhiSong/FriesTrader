# Phase A — Screening & Thesis Only (Automated Daily Task)

_Part of [FriesTrader](https://github.com/YizhiSong/FriesTrader), Copyright (c) 2026 Yizhi Song, MIT License._

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
ignore all other watchlists.

**Supplementary market scan** (additive, not a replacement): call
`run_scan` with `universe.supplementary_scan_id` — a saved Robinhood
scanner (relative volume and market cap criteria, see
`universe.supplementary_scan_note`) that surfaces genuinely notable
movers from outside your watchlist, so candidate selection isn't limited
to names you've personally added. Drop any scan result that's already on
the watchlist (it's already a watchlist candidate, not a second one) or
already a held position (always included regardless, per below). From
what's left, take up to `universe.supplementary_scan_max_candidates` —
the scan's own default ordering, no re-ranking needed — and mark each
`"source": "market_scan"` on its `screened` line (watchlist-sourced and
held candidates get `"source": "watchlist"`). This cap is **separate
from and additive to** `watchlist_max_candidates` below — scan results
never compete with watchlist candidates for the same slots.

Dedupe the combined (watchlist + capped scan) list, filter via
`get_equity_fundamentals` against `risk_rules.json`'s current `universe`
block, and cap the **watchlist-sourced, non-held** portion at
`universe.watchlist_max_candidates` — the scan's own separate cap above
already bounds its own contribution, so this cap only ever applies to
watchlist candidates.

Pull current prices for the capped candidate list via `get_equity_quotes`
(batched into one call), fresh every run. Use `last_trade_price` as
`current_price` in Steps 2–3.

Pull price history per candidate via `get_equity_historicals`
(`interval="day"`, spanning the last ~300 calendar days — enough to
cover a `trend_filter_lookback_trading_days`-bar moving average plus
buffer for weekends/holidays), fresh every run. This same series is
reused in Step 2 for the 60-day price-move signal and in the trend-filter
check just below — no second historicals call needed for either.

`universe.max_market_cap_usd` is a ceiling, not just a floor — exclude if
market cap exceeds it, regardless of how strong the candidate otherwise
looks. Log as
`"market cap $<X> exceeds universe.max_market_cap_usd ($<threshold>) — excluded per universe filters"`.

`universe.penny_stock_filter_enabled` is a mechanical exclusion, not a
judgment call, active only when true: exclude if current price <
`universe.penny_stock_price_threshold_usd`, regardless of how the stock
is otherwise trading. Log the reason as
`"penny stock (price $<X>, under $<threshold>) — excluded per universe.penny_stock_filter_enabled"`.

`universe.leveraged_etf_filter_enabled`/`universe.inverse_etf_filter_enabled`
are also mechanical, each independently toggleable: when true, exclude if
Step 1's `get_equity_fundamentals` `description` field contains
"leveraged" or "inverse" respectively (case-insensitive substring match)
— fund providers state this directly (e.g. TQQQ: "provides 3x leveraged
exposure...", SQQQ: "provides (-3x) inverse exposure..."), no judgment
about current risk needed. Log the reason as
`"leveraged/inverse ETF (description: \"<matched phrase>\") — excluded per universe.<leveraged_etf_filter_enabled|inverse_etf_filter_enabled>"`.

`universe.trend_filter_lookback_trading_days` is a mechanical downtrend
exclusion, active only when `universe.trend_filter_enabled` is true:
exclude the candidate if `current_price` is below the simple moving
average of its trailing `trend_filter_lookback_trading_days` daily
closes (from the historicals series pulled above), regardless of how
strong the candidate otherwise looks. Log as
`"200-day MA $<X>, current price $<Y> (<Z>% below trend) — excluded per universe.trend_filter_enabled"`.
If fewer than `trend_filter_lookback_trading_days` daily bars are
available (e.g. a recent IPO), skip this specific check for that
candidate rather than excluding or guessing, and log
`"trend filter skipped — fewer than <trend_filter_lookback_trading_days> daily bars available"`.

**Always ensure every held position is in the final list**
(`get_equity_positions`, account_number from `risk_rules.json`) — if one
already made it through on its own (e.g. it's also on the watchlist),
leave it as-is, don't add a duplicate. `watchlist_max_candidates` is a
cap on **non-held** candidates only: exclude held positions from that
count entirely before checking whether the cap was exceeded, so a held
position can never occupy a slot or cause a non-held candidate to be
dropped. A held position must stay eligible for a fresh thesis
(including `exit_existing`) and never get silently dropped for being
illiquid, small-cap, below its moving average, or off the list. Log as
`"stage": "screened", "passed_filters": true, "reason": "currently held — always included"`
regardless of what the filters would have said.

(This just builds the candidate list — not a risk/stop-loss check; that's
Phase B's job. See Hard stop below.)

## Step 2 — Gather signals

Use the ~210-day price history per candidate already pulled in Step 1
(`get_equity_historicals`) — no second pull needed; take its most recent
60 calendar days' worth of bars for the signal below. Never reuse
`close_60d_ago`, `latest_close`, or any other historicals-derived value
from a prior run's `pending_proposals.jsonl` or `trade_log.jsonl`, even
if today's figure looks unchanged from yesterday's — every number in `signal_check`
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
   (`high_52_weeks`/`low_52_weeks` from Step 1's `get_equity_fundamentals`
   call, `current_price` from Step 1's `get_equity_quotes` call).

**Log the raw inputs behind every ratio, not just the ratio** (see
`signal_check` format below) — otherwise it can't be sanity-checked
without re-pulling data.

If none apply, no search/thesis this run — log as `screened`-only.
Qualifying candidates' searches stay within
`cadence.news_search_budget_per_cycle` (per run, not per stock; held
positions draw from their own separate budget above, not this one).

**If more candidates qualify than the budget allows**, prioritize by how
far each one exceeded the specific threshold it tripped — not conviction
or `risk_flags` (those don't exist yet; they're outputs of the search
this budget gates, not inputs to it). Compute a **magnitude score** per
qualifying candidate:
- Price move: `actual_price_move_60d_pct / price_move_60d_pct` (threshold).
- Volume spike: `actual_volume_spike / volume_spike_multiple` (threshold).
- 52-week extreme: `pct_from_52wk_extreme` (threshold) `/ actual_pct_from_52wk_extreme`
  (whichever of the two 52-week-extreme ratios triggered) — inverted,
  since smaller = closer to the extreme = more notable.
If a candidate qualifies under more than one criterion, use its
**highest** score. Process qualifying candidates in descending score
order, spending the budget as you go. Any candidate that would push
spend past `cadence.news_search_budget_per_cycle` is skipped this
cycle — log
`"stage": "screened", "passed_filters": true, "reason": "news search budget exhausted this cycle (<N> of <cadence.news_search_budget_per_cycle> already spent on higher-magnitude signals) — no thesis this run"`.
It remains a normal candidate next cycle, re-screened fresh (no
carryover priority).

**Every qualifying candidate's search — new entry or held position —**
must explicitly check, in addition to whatever catalyst-specific query
satisfied Step 2:
1. Whether any active lawsuit/regulatory investigation naming the
   company has a scheduled ruling, hearing, trial date, or compliance
   deadline in the next ~90 days (the `active_litigation` risk_flags
   criterion below).
2. Whether the company has a confirmed accounting restatement,
   for-cause auditor dismissal/resignation, or an indictment/plea
   involving a current or former executive or employee tied to company
   operations, disclosed within the last 3 years (the
   `governance_history` risk_flags criterion below).
Don't rely on either surfacing incidentally from a catalyst-only search
— a stock can carry an open investigation or a past scandal
indefinitely without any day's catalyst search happening to mention it.

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

**`conviction` follows a fixed rubric, not open judgment** — the same
underlying facts must produce the same rating regardless of which day
this runs. Evaluate fresh each run using only what this run's own
research found; never carry forward or average against a prior day's
conviction for the same symbol.

- **`high`** requires **all** of:
  - The catalyst is a specific, already-confirmed, company-disclosed
    event (an earnings result, a signed deal/contract, a completed
    regulatory approval, a disclosed structural risk) — not a rumor,
    analyst opinion, technical pattern, or sector/macro-wide move, and
    not still pending/anticipated (e.g. "ahead of earnings" caps at
    `medium` no matter how bullish/bearish the setup sounds).
  - The thesis explicitly names the strongest available counter-evidence
    (a plausible positive if bearish, a plausible negative if bullish)
    and gives a concrete reason it doesn't change the read — silence on
    the counter-case, or listing it without resolving it, doesn't
    qualify. ("...regardless of X" / "even though X" / "even with X" —
    not just piling on more confirming evidence.)
  - `risk_flags` is empty (see below).
  - No unresolved binary catalyst (earnings date, court ruling,
    regulatory deadline) falls before this position's next likely
    review that could reverse the read.
- **`low`** applies if **any** of:
  - The thesis itself frames the evidence as mixed, offsetting, or
    unresolved (e.g. "mixed," "offset by," "still isn't fully
    confident," "unpredictable") rather than reaching a clear net read.
  - The move is explained as technical, mechanical, or sentiment-driven
    in a way that discounts its fundamental significance (e.g. "largely
    mechanical," "sentiment-driven rather than a disclosed fundamental
    deterioration").
  - Any `risk_flags` entry is present.
  - The catalyst is macro/sector-wide rather than company-specific
    (e.g. "broad rotation," "sector sentiment").
- **`medium`** is everything else: a real, credible, company-specific
  catalyst exists and doesn't hit a `low` disqualifier, but the catalyst
  is still pending, or multiple contributing factors are listed without
  one clearly resolved as dominant, or no counter-case is explicitly
  engaged and dismissed.

**For a held position**, `direction` is `"long"` (still supports holding)
or `"exit_existing"` (no longer does) — never `"avoid"` (that's only for
not-yet-held candidates).

**Include `risk_flags`** for every `direction: "long"` candidate — an
array of zero or more tags from this fixed set, based only on what this
run's sourced research already found (no extra searches):
- `"active_litigation"` — an active lawsuit or regulatory investigation
  naming the company or an executive, with a specific scheduled ruling,
  hearing, trial date, or compliance deadline within the next ~90 days.
  An open-ended investigation or long-running dispute with no scheduled
  next step doesn't qualify on its own (most large companies have one
  of these at any given time) — mention it in the thesis narrative if
  relevant, but don't flag it.
- `"governance_history"` — a confirmed accounting restatement, for-cause
  auditor dismissal/resignation, or an indictment/plea involving a
  current or former executive or employee tied to company operations,
  disclosed within the last 3 years. Must be a completed, sourced event
  (company filing, regulatory action, or named-source reporting) — a
  short-seller report alone, a rumor, or an investigation with no
  confirmed finding yet doesn't qualify on its own (that's
  `active_litigation`'s territory if it has a scheduled next step, or
  just thesis-narrative color otherwise). The point is a track record of
  already-happened failures, not a prediction about an unresolved one.
- `"dilution_risk"` — a completed or pending equity/convertible raise,
  share offering, or ATM program disclosed in the last ~90 days.
- `"insolvency_or_liquidity_concern"` — bankruptcy rumor, going-concern
  language, or reliance on an external backer to remain solvent.
- `"leadership_turnover"` — a C-suite departure/replacement in the last
  ~90 days tied to operational or execution problems (not routine
  succession).
Empty array (`[]`) if none apply. Used by Phase B (Step 7) as the
primary within-tier tie-break, ahead of `pct_below_52wk_high`.

**Include `pct_below_52wk_high`** for every `direction: "long"` candidate:
`(high_52_weeks - current_price) / high_52_weeks` (e.g. `0.15`).
`high_52_weeks` from Step 1's `get_equity_fundamentals` call,
`current_price` from Step 1's `get_equity_quotes` call. Used by
Phase B (Step 7) as the secondary within-tier tie-break, after
`risk_flags` — a disclosed "room in the setup" proxy, not a fair-value
calc. Omit for `avoid`/`exit_existing`.

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

**Overwrite `pending_proposals.jsonl` at the start of this run** — it
should hold only today's candidates; history remains auditable via
`trade_log.jsonl`, which Phase B writes to when acting on a proposal.

Every line needs a real `"timestamp"` (`HH:mm:ss`, e.g. via
`TZ='America/Chicago' date +'%H:%M:%S'` — never guessed) alongside
`"date"`. Time-of-day only, no date prefix. For human readability only —
never used for idempotency or other logic.

Write:
- One `"stage": "screened"` line per candidate (`passed_filters`,
  `source` (`"watchlist"` or `"market_scan"`), `avg_volume`,
  `market_cap`, `reason` if rejected — shape matches
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

Do not touch `trade_log.jsonl` — reserved for Steps 4–9 (Phase B), which
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
{"date": "YYYY-MM-DD", "timestamp": "HH:mm:ss", "stage": "summary", "decision": "long", "symbols": ["AAPL (medium)", "AMD (high)", "..."]}
{"date": "YYYY-MM-DD", "timestamp": "HH:mm:ss", "stage": "summary", "decision": "exit_existing", "symbols": []}
```

`rejected` = failed universe filter. `no_signal` = passed filters, no
Step 2 signal/thesis. `avoid`/`long`/`exit_existing` = matches the
thesis's `direction`. Plain symbol lists throughout, except `long`
appends each symbol's own thesis `conviction` as `"<symbol>
(<conviction>)"` — the one bucket where it drives sizing; the others
carry a conviction too, it's just not decision-relevant there. No other
reason/detail fields — a quick-glance list, not a substitute for the
thesis lines.

## Hard stop

Do not call `review_equity_order`, `place_equity_order`, or any
cancel/order tool, or check `execution.mode`. `get_equity_positions` is
for Step 1's candidate list only — no stop-loss/drawdown computation
here; all risk enforcement is Phase B's job.
