# Phase B — Re-verify, Risk Enforcement, Order Review/Execution, and Logging (Automated Daily Task)

_Part of [FriesTrader](https://github.com/YizhiSong/FriesTrader), Copyright (c) 2026 Yizhi Song, MIT License._

Automated second half of this pipeline (see `README.md`), run every
weekday 8:35am Central (5 min after 9:30am ET open) as a cloud routine.
Performs **Steps 4–7**, consuming candidates from Phase A's
`pending_proposals.jsonl`.

Authorized to place real live orders under a narrow condition (see Step
5's "Live-order gate" — Step 6 reuses the identical gate for buys).
**That authorization must be given explicitly, in advance, by whoever
operates this pipeline — after being warned that an unattended
scheduled task has no human confirmation at the moment of execution.**
Do not add, remove, or loosen any gate condition on your own judgment.

## Step 0 — Load state (do this first, every run)

1. Read `risk_rules.json` **fresh** — never cache across runs. Use its
   `account_number`, not a hardcoded value.
2. Determine today's day of week mechanically (e.g.
   `TZ='America/Chicago' date +'%A'`) — don't infer it from the date
   string. Needed for Step 6's weekend-gap check.
3. Read `pending_proposals.jsonl` (overwritten each Phase A run, holds only
   the latest run — use its `"stage": "thesis"` entries directly as
   today's candidates). If missing or empty, log a `cycle_summary` noting
   nothing to process and stop — don't error.
4. Read `trade_log.jsonl` (if present):
   - **Idempotency — key off the proposal's own `date`, not today's.**
     Skip a candidate if `trade_log.jsonl` already has a `risk_check`/
     `order` entry for that symbol with a matching `proposal_date` (not
     the entry's top-level `date`, which reflects when the decision was
     made and changes daily even for a stale proposal). This matters
     because if Phase A ever fails to run, an un-refreshed proposal would
     otherwise look "new" every day and could be re-bought repeatedly;
     keying off `proposal_date` means it's decided once. `stop_loss`/
     `take_profit` are exempt — always run fresh.
   - **Dry-run cycle count**: number of **distinct dates** with a
     `cycle_summary` entry where `mode: dry_run` — not raw entry count
     (same-day reruns count once). This represents validated days, not
     executions, and must be
     `>= execution.dry_run_min_cycles_before_live` before the
     live-order gate (Step 5 for sells, Step 6 for buys) can open.

## Step 4 — Determine sells and re-verify buy candidates

### Stop-loss check (always runs, independent of new candidates)

Gather inputs, then let the script decide — do not hand-compute the
reference price, drawdown, stdev, or clamp. For each open position:
- Pull current `get_equity_positions` and a fresh `get_equity_quotes`
  price.
- Check `trade_log.jsonl` for whether any `take_profit` tier has fired
  for this position's current holding period (same "since quantity
  last reached zero" scope as the take-profit check below).
- If a tier has fired, pull daily `high_price` bars via
  `get_equity_historicals` (interval=day, split-adjusted) from the
  holding period's entry date (the buy that started it from zero)
  through yesterday, for `--daily-highs`.
- If `risk_rules.json`'s `stop_loss.mode` is `"volatility_scaled"` and
  the position is not currently showing a gain on average cost, pull
  the last `stop_loss.volatility_lookback_trading_days` trading days
  of daily closes via `get_equity_historicals` (interval=day,
  split-adjusted; request ~30 calendar days back to cover
  weekends/holidays, drop any `interpolated: true` bars), oldest
  first through yesterday, for `--daily-closes`. Skip this pull on a
  gain — the script itself also skips the computation in that case,
  since a non-positive drawdown can never meet a positive `stop_pct`.

Run:
`python3 scripts/stop_loss.py --average-cost <avg cost> --current-price <fresh quote> --mode <stop_loss.mode> --hard-stop-pct <stop_loss.hard_stop_pct> --volatility-multiplier <stop_loss.volatility_stdev_multiplier> --min-stop-pct <stop_loss.min_stop_pct> --max-stop-pct <stop_loss.max_stop_pct> --fallback-stop-pct <stop_loss.fallback_stop_pct> --min-bars 10 [--daily-closes <comma-separated closes>] [--take-profit-tier-fired --daily-highs <comma-separated highs> --trailing-high-since <entry date>]`
and use its JSON output directly (`stop_reference_basis`,
`stop_reference_price`, `drawdown_pct`, `stop_pct_used`, `stdev_20d`
when computed, `fallback_reason` when the fallback applied,
`triggered`, `action`) rather than recomputing any of it. This only
changes what the stop protects — it does not affect the take-profit
gain calculation below, which always measures gain from average cost
regardless of `stop_reference_basis`.

**If the script fails to run**, do not guess a result: treat this
position as if a loss-limit breach applied this cycle (`entries_halted
= true` for new entries/top-ups, this position itself excluded from
any sell decision) and log `"stage": "stop_loss"` with
`"stop_pct_used": null, "triggered": false, "action":
"halt_entries_check_manually", "notes": "stop_loss.py failed to run —
verify this position's stop manually before next cycle"`. Do not fall
back to manual computation.

If `triggered` is true — immediate full-position sell, no thesis
review, never blocked by a loss-limit halt, executed in Step 5's
sell-execution pass. A good thesis never cancels a stop-loss — see
`risk_rules.json`'s note. Log `"stage": "stop_loss"` with:
- the script's `stop_pct_used`, `stop_reference_basis`,
  `stop_reference_price`
- `trailing_high_since`, when trailing
- the script's own `"notes": "gain, stop not computed"` as-is, when
  `stop_pct_used` is null
- `stdev_20d`/`fallback_reason`, when present

### Take-profit check (always runs, independent of new candidates, tiered partial sells)

Using the same pull as the stop-loss check (no need to
call again — average cost, quantity, and fresh price as they stood at
the start of this step, before any of this cycle's sells execute in
Step 5), check `trade_log.jsonl` for `"stage": "take_profit"` entries
for this symbol at each tier's exact `gain_pct`, logged since the
position's quantity last reached zero (a full exit) — collect the
`gain_pct` values already fired this holding period.

Run:
`python3 scripts/take_profit.py --average-cost <avg cost> --current-price <fresh quote> --quantity <quantity> --tiers <risk_rules.json take_profit.tiers as "gain_pct:sell_fraction" pairs, e.g. "0.15:0.25,0.30:0.25,0.50:0.25"> [--already-fired <comma-separated gain_pct values already fired this holding period>]`
and use its JSON output directly (`gain_pct`, `tiers_status`,
`fired_this_cycle`, `triggered`, `action`) rather than recomputing any
of it — the script already handles the ascending-order,
cascading-quantity logic for **when a single cycle's gain has jumped
past more than one not-yet-fired tier at once**.

**If the script fails to run**, treat it like a stop-loss script
failure: `entries_halted = true` for new entries/top-ups this cycle,
exclude this position from any sell decision, and log `"stage":
"take_profit"` with `"triggered": false, "action":
"halt_entries_check_manually", "notes": "take_profit.py failed to run
— verify this position's tiers manually before next cycle"`. Do not
fall back to manual computation.

**If `fired_this_cycle` is non-empty** — each fired tier is executed
in Step 5's sell-execution pass, no thesis review, never blocked by a
loss-limit halt (it's an exit, not a new entry). Log one line per
entry in it (already in ascending order), `"stage": "take_profit",
"triggered": true, "action": "sell_partial_position"`, with:
- `"tier_gain_pct"`, `"sell_fraction"`, `"quantity_before"`,
  `"quantity_sold"` straight from that entry
- the script's top-level `"gain_pct"` and `"tiers_status"`

**If `fired_this_cycle` is empty**, log one line with the script's
`gain_pct` and `tiers_status`, `"triggered": false, "action":
"hold_monitor"`.

Once all three tiers have fired, the remaining quantity is held long
indefinitely — only the stop-loss check above still applies to it.
Tiers become eligible again only after the position is fully closed to
zero shares and a new entry is later opened (a genuinely new holding
period, not a top-up).

### Conviction-trim check (held positions only, mechanical rebalance-down)

Skip entirely if `risk_rules.json`'s `conviction_trim.enabled` is `false`.
Otherwise, for every **held** position, using this cycle's fresh
`conviction` and `target_size` (from the same inputs gathered for Step
6's priority-order ranking — pull those first if not yet available),
look back through this symbol's `risk_check` entries in `trade_log.jsonl`,
most recent first, and count consecutive entries (not including this
cycle, and not crossing back over a prior full exit to zero) where
`conviction == "low"` and `current_position_value` exceeded `target_size`
by more than `conviction_trim.overweight_trigger_pct`.

Run:
`python3 scripts/conviction_trim.py --conviction <this cycle's conviction> --current-position-value <current_position_value> --target-size <target_size> --overweight-trigger-pct <conviction_trim.overweight_trigger_pct> --prior-consecutive-low-overweight-cycles <count from the lookback above> --min-low-conviction-cycles <conviction_trim.min_low_conviction_cycles>`
and use its JSON output directly (`overweight_pct`, `qualifies_this_cycle`,
`consecutive_cycles`, `triggered`, `trim_dollar_amount`, `action`) rather
than recomputing any of it.

**If the script fails to run**, treat it like a stop-loss/take-profit
script failure: `entries_halted = true` for new entries/top-ups this
cycle, exclude this position from any sell decision, and log `"stage":
"conviction_trim"` with `"triggered": false, "action":
"halt_entries_check_manually", "notes": "conviction_trim.py failed to
run — verify this position manually before next cycle"`.

**If `triggered` is true** — sell `trim_dollar_amount` worth of the
position (round to a quantity the broker accepts), down to
`target_size`, executed in Step 5's sell-execution pass, never blocked
by a loss-limit halt, same as stop-loss/take-profit. Log `"stage":
"conviction_trim"` with the script's `overweight_pct`,
`consecutive_cycles`, `trim_dollar_amount`, `"triggered": true,
"action": "sell_partial_position"`.

**If `triggered` is false**, log one line with the script's
`overweight_pct`, `qualifies_this_cycle`, `consecutive_cycles`,
`"action": "hold_monitor"`.

Applies only to **held** positions — never a **new**-group candidate,
which has no existing position to be overweight in.

### Classify candidates

`direction: "avoid"` candidates aren't processed further (already
logged in Phase A). `exit_existing` candidates (Phase A's
recommendation to sell a currently-held position) go straight into
Step 5's sell-execution pass — selling is never gated. Split the
remaining `direction: "long"` candidates, using this snapshot's
`get_equity_positions` (before this cycle's sells
execute), into:
- **new**: not a live open position — a genuine new entry, the only
  kind that consumes a slot.
- **held**: already a live open position — a potential top-up
  (Step 6). Top-ups never consume a slot and are always considered
  regardless of account fullness.

**This classification stays fixed for the rest of the cycle**, even
if a same-cycle sell later empties the position — otherwise a
symbol whose stop-loss fires this cycle would silently shift from
**held** to **new** by the time Step 6 runs, and Step 6's
same-cycle sell-then-buy guard (which operates on the **held**
group) would no longer find it there.

## Step 5 — Sell-side execution

**Execute sells now — every stop-loss trigger, fired take-profit tier,
conviction-trim trigger, and Step 4's `exit_existing` candidates:** run
this procedure for each, before touching anything buy-side below — a
sell must actually clear before its freed cash/slot can be counted
toward a same-cycle buy, and before today's realized P&L (loss-limit
check, further down) can see it.

1. **Always** call `review_equity_order` first — a preview, never places
   anything.
2. If it surfaces a blocking alert, do not proceed to placement
   regardless of mode; log the alert verbatim and treat as rejected.
3. Otherwise, branch on `execution.mode` (fresh from Step 0) and the
   dry-run cycle count:

   **Live-order gate — ALL must be true:**
   - `execution.mode == "live"`
   - dry-run cycle count `>= execution.dry_run_min_cycles_before_live`
   - `review_equity_order` for this order returned no blocking alert

   - **Gate open**: call `place_equity_order` with the reviewed
     parameters. Then confirm the real fill before logging — the
     `place_equity_order` response alone is not enough (it typically
     returns `order_state: "unconfirmed"`, not the actual outcome):
     1. Call `get_equity_orders` with this `order_id`.
     2. If `state` is terminal (`filled`, `partially_filled`,
        `cancelled`, `rejected`, `failed`, `voided`), use it.
     3. Otherwise wait ~15 seconds and check once more; use whatever
        `state` comes back, terminal or not — never poll more than
        twice or block the cycle waiting for a fill.
     Log `"stage": "order", "mode": "live", "placed": true, "order_id":
     "<id>", "order_state": "<confirmed state from get_equity_orders>",
     "fill_price": <average_price if filled/partially_filled, else
     null>, "fill_quantity": <cumulative_quantity if filled/partially_filled,
     else null>` in addition to the pre-trade `quote_bid`/`quantity`
     estimate already logged (not in place of it) — the log should show
     both the estimate and the confirmed real outcome.
   - `execution.mode == "dry_run"`: log
     `"stage": "order", "mode": "dry_run", "would_execute": true"` and stop.
     **Never call `place_equity_order` here.**
   - `execution.mode == "live"` but cycle count still under threshold: do
     **not** place. Log
     `"stage": "order", "mode": "live_blocked_insufficient_cycles", "would_execute": true, "placed": false"`
     with current vs. required count.

Never invent/guess a field value — if a tool call fails, log the
failure and skip that candidate. Every `order` entry must carry
`proposal_date` (Step 0's idempotency key).

**Wash-sale flag on sells (informational only, never blocks a sell):**
whenever the stop-loss check triggers, a take-profit tier fires, a
conviction-trim fires, or an
`exit_existing` sell is processed, and that specific sale realizes a
loss (a stop-loss sell is always a loss by definition; check
take-profit/`exit_existing` case by case against the fill), check the
same `wash_sale_avoidance.linked_accounts` for a purchase of that symbol
within `lookback_window_days` days before today.

A qualifying purchase alone is not enough to flag — the wash-sale rule
disallows the loss by rolling it into the cost basis of stock you still
hold, so if nothing of that symbol remains held anywhere after this
sale, there is no replacement position for a disallowed loss to attach
to and it is not a wash sale, whatever the calendar gap. Concretely: a
single purchase fully closed out by this same sale (that account's
position in the symbol is now zero, and no other linked account holds
or separately purchased the symbol within the window) is an ordinary
closed round-trip, not a wash sale — do not flag it. Before adding the
flag, call `get_equity_positions` for every account in
`wash_sale_avoidance.linked_accounts` and confirm at least one of them
still holds a nonzero quantity of the symbol after this sale, sourced
from a purchase inside the lookback window (i.e. a genuine surviving
replacement lot, not the shares this sale just closed out). Only then
add
`"wash_sale_flag": true, "wash_sale_note": "possible wash sale -- <symbol> was bought in account <account_number> on <date>, within <lookback_window_days> days of this sale, and a replacement position remains held in account <holding_account_number> -- this loss may be disallowed (or, if <holding_account_number> is an IRA, permanently disallowed) for tax purposes"`
to that sell's `order` log entry. Purely informational for the human's
own tax reconciliation — it never blocks, delays, or resizes the sell
itself, and it does not require `wash_sale_avoidance.enabled` to be
`true` (the flag is a record of what happened, not a guard against
future action, so it stays on even if the buy-side guard is toggled
off). Note the asymmetry this can't fix: a sell logged clean today can
still become a wash sale later if a linked account buys the same symbol
afterward — that's outside this pipeline's visibility and control.

**Re-pull fresh account state (now reflects this cycle's executed
sells, not an estimate):** call `get_portfolio` (for `total_value` and
`cash`) and `get_equity_positions` (for the live open position count)
again — the earlier pull is now stale for any sell that actually
executed above. Use these fresh values for Step 6's capacity
computation, loss-limit check, and candidate sizing below. In
`dry_run` mode these won't have changed (nothing real executed),
which is expected — this pull only matters once `execution.mode` is
`"live"`.

## Step 6 — Buy-side risk enforcement, sizing, and execution

**Compute capacity (using Step 5's fresh re-pulled account state, not
an estimate):** `open_slots = max_concurrent_positions - (live
positions per the re-pulled get_equity_positions above)`. Only
**new**-group candidates (Step 4's classification) consume a slot;
fixed for the rest of this cycle unless one gets approved below.

**If `open_slots <= 0`**: no **new** candidate can be approved this
cycle. Skip the weekend-gap search and buy gate below for every
**new**-group candidate — log instead:
`"stage": "risk_check", "passed": false, "proposal_date": "<candidate's date from pending_proposals.jsonl>", "reason": "no open slots this cycle (X of Y max already held/approved) — skipped without staleness re-check"`.
**held** group is unaffected.

**If `open_slots > 0`**: **new** group continues normally (slots may
still run out mid-ranking via ordinary per-candidate concurrency check).

**No same-cycle sell-then-buy**: if a symbol's stop-loss fired, any
take-profit tier fired, or a conviction-trim fired earlier this cycle
(Step 4), it is not eligible for a top-up this same cycle, regardless
of thesis or conviction — drop it from the **held** group before
continuing, logging
`"stage": "risk_check", "passed": false, "position_action": "top_up", "reason": "stop-loss/take-profit fired this cycle — not eligible for a same-cycle top-up"`.
This applies unconditionally (dry_run or live) since it's about not
producing a self-contradictory sell-and-buy decision within one cycle,
not about whether the sell actually executed. It's a normal top-up
candidate again starting next cycle (subject to the buy gate below,
including the sell re-entry lock). Checking this before the weekend-gap
search and buy gate below avoids spending news-search budget or API
calls on a candidate that's getting dropped regardless.

For every **new** candidate not short-circuited above, and every
**held** candidate not dropped above:

### Weekend gap (Monday runs only)

A Friday proposal is staler than an overnight one — 2.5 days vs ~16
hours. **If today is Monday**:

1. Before the price-staleness check, run **one additional targeted search
   per pending proposal** covering Saturday/Sunday (earnings, M&A,
   guidance, macro) — separate from and not counted against
   `cadence.news_search_budget_per_cycle`. Same sourcing rules as Phase
   A's thesis `sources` field (prefer primary/major-outlet sources; cite
   whatever you used).
2. If anything materially contradicts the thesis/invalidation criteria,
   drop it — log
   `"stage": "risk_check", "passed": false, "proposal_date": "<candidate's date from pending_proposals.jsonl>", "reason": "weekend news invalidated thesis: <what you found>", "sources": ["Outlet Name: https://..."]`
   — don't process further.
3. If nothing turns up, proceed to the price-based check.

Other weekdays: skip straight to the price-based check.

### Buy gate (every day)

**Buys only — new entries and top-ups; never applies to
stop_loss/take_profit/conviction_trim/exit_existing sells, which are
never gated on price or tax considerations.** One script call answers
every independent, per-symbol condition that can block this candidate;
`position_sizing.py`'s slot/cash allocation across the whole candidate
list is a separate, later check further down in this same step, since
it depends on other candidates, not just this one.

Pull a fresh quote (`get_equity_quotes`) — re-verify against this
morning's open, not Phase A's prior-close price.

**Gather inputs, then let the script decide — do not hand-compute any
gap, average, or lock condition:**
- `--fresh-ask`: this morning's fresh `ask`.
- `--thesis-price`: Phase A's thesis-time `current_price`.
- `--daily-closes`: the last `entry_extension.lookback_trading_days`
  trading days of daily closes via `get_equity_historicals`
  (interval=day, split-adjusted; request ~30 calendar days back to
  cover weekends/holidays, drop any `interpolated: true` bars).
- Wash-sale inputs, only if `wash_sale_avoidance.enabled` is `true`
  (pass `--wash-sale-enabled` and `--wash-sale-lookback-days
  <wash_sale_avoidance.lookback_window_days>`; omit both otherwise):
  for every account number in `wash_sale_avoidance.linked_accounts`,
  call `get_pnl_trade_history` filtered to this symbol and collect the
  dates of any closing trade realizing a negative gain, as
  `--loss-sale-dates <comma-separated ISO dates>` (omit if none
  found). Also pass `--today <today's date, ISO>`.
- Sell re-entry lock inputs, only if `trade_log.jsonl` has this
  symbol's most recent sell `order` entry (`stop_loss`, `take_profit`,
  `conviction_trim`, or `exit_existing`) and it actually executed —
  confirm via `get_equity_positions` that quantity is genuinely lower
  than immediately before that logged sell, or the position was fully
  closed and re-opened since. A `dry_run` sell never actually reduces
  the position, so if quantity is unchanged there was no real
  reduction and these inputs should be omitted entirely (evaluate the
  symbol normally). If it did execute, pass `--last-sell-reason
  <reason>`, `--last-sell-price <that entry's quote_bid>`,
  `--last-sell-date <that entry's date>`; for `take_profit` or
  `conviction_trim` reasons only, also pass
  `--reentry-lock-max-trading-days <take_profit's or
  conviction_trim's reentry_lock_max_trading_days, matching the
  reason>` and `--trading-days-since-sell <trading days elapsed since
  that sell date>`.

Run:
`python3 scripts/entry_gate.py --fresh-ask <ask> --thesis-price
<thesis current_price> --entry-price-gap-max-pct
<entry_price_gap.max_pct> --daily-closes <comma-separated closes>
--max-extension-pct <entry_extension.max_extension_pct>
[--wash-sale-enabled --wash-sale-lookback-days <N> --loss-sale-dates
<dates>] --today <date> [--last-sell-reason <reason> --last-sell-price
<price> --last-sell-date <date> [--reentry-lock-max-trading-days <N>
--trading-days-since-sell <N>]]`
and use its JSON output (`entry_price_gap`, `entry_extension`,
`wash_sale_avoidance`, `sell_reentry_lock`, `passed`,
`blocking_conditions`, `action`) directly rather than recomputing any
of it.

**If the script fails to run**, do not guess a result — skip the buy
this cycle and log `"stage": "risk_check", "passed": false,
"proposal_date": "<candidate's date>", "reason": "entry_gate.py failed
to run — buy skipped this cycle, verify manually"`.

**If `passed` is false**, skip the buy this cycle regardless of how
strong the thesis still reads — mechanical, not a judgment call, same
as a stop-loss. The candidate isn't blacklisted, just re-evaluated
fresh on the next Phase A run (or, for the re-entry lock, once its own
clearing condition is met). Log one `"stage": "risk_check", "passed":
false, "proposal_date": "<candidate's date>"` line per entry in
`blocking_conditions`, using the matching reason text:
- `entry_price_gap`: `"reason": "price gapped <gap_pct>% above
  thesis-time price ($<thesis-price> -> $<fresh-ask>), exceeds
  entry_price_gap.max_pct (<threshold>) -- buy skipped this cycle"`
- `entry_extension`: `"reason": "price <extension_pct>% above its
  <N>-day average ($<moving_avg> -> $<fresh-ask>), exceeds
  entry_extension.max_extension_pct (<threshold>) -- buy skipped this
  cycle"`
- `wash_sale_avoidance`: `"reason": "wash sale guard -- <symbol> was
  sold at a loss within the <lookback_window_days>-day wash-sale
  window (<matching_loss_sale_date>)"` (add `"position_action":
  "top_up"` if it's a top-up candidate)
- `sell_reentry_lock`: `"reason": "sell re-entry lock — current price
  <fresh-ask> is above the <last-sell-price> it was sold at on
  <last-sell-date> (reason: <last-sell-reason>)"` (for a `take_profit`
  or `conviction_trim` lock still active only on the time condition,
  append `", N of <max> trading days elapsed"`)

**If `passed` is true but `entry_price_gap.gap_pct` is still
non-trivial**, re-check against the thesis's `invalidation` criteria —
same sourcing rules as Phase A's thesis `sources` field (prefer
primary/major-outlet sources; cite whatever you used), citing whatever
explains the gap in a `"sources"` field on the resulting log line; if
it plausibly invalidates the thesis even under the hard ceiling, drop
it — log `"stage": "risk_check", "passed": false, "proposal_date":
"<candidate's date>", "reason": "weekend news invalidated thesis: <what
you found>", "sources": ["Outlet Name: https://..."]`, same as the
weekend-gap check above.

**Loss-limit halt check (always runs, gates all new entries and top-ups):**
Call `get_realized_pnl` span=day and span=week (asset_classes=[equity])
for today's and this week's realized `total_returns` in dollars (0 if no
trades). Do **not** hand-compute the percentages — run
`python3 scripts/pnl_pct.py --daily-realized-usd <day total_returns> --weekly-realized-usd <week total_returns> --starting-capital-usd <risk_rules.json starting_capital_usd> --daily-limit-pct <loss_limits.daily_loss_limit_pct_of_account> --weekly-limit-pct <loss_limits.weekly_loss_limit_pct_of_account>`
and use its JSON output (`daily_pnl_pct`, `weekly_pnl_pct`,
`entries_halted`, `halt_reason`) directly. **If the script fails to run
or `get_realized_pnl` can't be determined cleanly, fail safe: treat as
breached** (`entries_halted = true`) rather than falling back to manual
computation. Halts both new entries and top-ups (a top-up still spends
cash/exposure, even though it skips the concurrency check).
Log as `"stage": "loss_limit_check"`.

**Candidate priority order — new entries and top-ups compete equally
(decide before any per-candidate check):**
Merge **new** and **held** groups from Step 4's classification
(excluding new-group candidates already rejected by this step's
capacity short-circuit, same-cycle guard, weekend-gap check, or buy
gate above) into one list. **Do not hand-sort or hand-compute any of
this — gather the inputs below and let the scripts decide.**

**Gather, for every candidate in the merged list:** `symbol`,
`conviction`, `risk_flags` (omit the key entirely if the thesis
disclosed none — an omitted key and an empty array mean different
things to the ranking script below), `pct_below_52wk_high` (omit if
not available), `group` (`"new"` or `"held"`), and — for **held**
candidates only — `current_position_value` (quantity from the fresh
re-pulled `get_equity_positions` above × fresh price from
`get_equity_quotes`). Also use the re-pulled `total_value` and `cash`
from `get_portfolio` above (`cash` is the starting `cash_remaining`),
and `concurrent_positions_start` (the `open_slots` computation above).

Rank the candidates, then size them — pipe the candidate list (a JSON
array) through both scripts in sequence (directly chainable):
`python3 scripts/rank_candidates.py | python3 scripts/position_sizing.py --total-value <total_value> --cash-start <cash> --concurrent-positions-start <concurrent_positions_start> [--entries-halted] --max-position-pct <position_sizing.max_position_pct_of_account> --max-concurrent-positions <position_sizing.max_concurrent_positions> --min-cash-buffer-pct <position_sizing.min_cash_buffer_pct> --min-top-up-usd <position_sizing.min_top_up_usd> --min-top-up-pct-of-target <position_sizing.min_top_up_pct_of_target> --conviction-pct "high:0.20,medium:0.12,low:0.06"`
`rank_candidates.py` sorts by conviction tier first (`high` before
`medium` before `low`), then `risk_flags` count ascending within a
tier (a missing `risk_flags` field sorts last, treated as worst
case), then `pct_below_52wk_high` descending (a missing field sorts
last in its tier) — a high-conviction top-up can end up ranked ahead
of a lower-conviction new entry and vice versa.
`position_sizing.py` then processes strictly in that ranked order,
compounding the running cash/concurrency totals as each candidate is
approved. Pass `--entries-halted` whenever the loss-limit check above
(or a stop-loss/take-profit script failure) halted entries this cycle
— the sizing script then rejects every candidate uniformly with the
standard halt reason and leaves the totals unchanged.

**Log `risk_flags` and `pct_below_52wk_high` as structured fields on
every risk_check entry from this sort — winners and rejections alike**
(the only place this survives, since `pending_proposals.jsonl` is
overwritten daily).

Use the final script's JSON output directly — rather than recomputing
any of it:
- its `results` array: one entry per candidate, in ranked order, each
  carrying `passed`, and depending on outcome: `reason`,
  `position_action: "top_up"`, `dollar_amount`,
  `current_position_value`, `target_size`, `headroom`,
  `concurrent_positions_after`, `cash_remaining_after`,
  `cash_buffer_after_pct`
- top-level `cash_remaining_final` and
  `concurrent_positions_after_final`

A **new**-group candidate rejected purely for lack of slots carries
the script's own `"concurrent_positions_after (N) exceeds
max_concurrent_positions (M) — cap filled by higher-priority
candidates this cycle"` wording, to show it's scarcity, not quality.

**If either script fails to run**, do not guess a result: reject every
still-pending candidate this cycle — log `"stage": "risk_check",
"passed": false, "reason": "rank_candidates.py/position_sizing.py
failed to run — no orders attempted this cycle, verify manually"` for
each — rather than falling back to manual computation.

For each candidate, log `"stage": "risk_check"` with that candidate's
`results` entry fields verbatim.

Every `risk_check` entry must include `proposal_date` (copied from the
candidate's `"date"` in `pending_proposals.jsonl` — Step 0's idempotency
key) and, for `direction: "long"`, `risk_flags` and `pct_below_52wk_high`
(for auditing the priority sort). Top-up entries must also include
`"position_action": "top_up"`.

**Execute approved buys:** for every candidate the sizing above
approved (new entries and top-ups), in ranked order, run the exact
same review → live-order-gate → place → confirm procedure as Step 5's
"Execute sells now" (`review_equity_order` first, the same Live-order
gate conditions, the same fill-confirmation and logging shape) — with
one difference: the pre-trade estimate logged alongside each order is
`quote_ask`/`quantity` here (a buy fills near the ask), not the
`quote_bid` used for sells.

Never change `execution.mode` yourself. Every `order` entry must carry
`proposal_date` (same as this step's `risk_check` entries) — Step 0's
idempotency check matches against either a `risk_check` or `order`
entry.

## Step 7 — Logging

Append every decision to `trade_log.jsonl` — one JSON line each:
`stop_loss`, `take_profit`, `conviction_trim`, `loss_limit_check`, `risk_check` (pass/fail,
including Step 6's weekend-gap rejections, buy-gate rejections
(price-gap, extension, wash-sale, sell re-entry lock), and top-up
evaluations), and `order` stages, matching the shape already in
`trade_log.jsonl`/`trade_log_template.jsonl`. Top-up entries must include
`"position_action": "top_up"`.

**Every line — including the final `cycle_summary` — needs a real
`"timestamp"`** (`HH:mm:ss`, e.g. via `TZ='America/Chicago' date +'%H:%M:%S'`
— never guessed), no date prefix. Separate from `"date"`/`"proposal_date"`
— for readability only, never used for idempotency, dry-run count, or
other logic; only `date` and `proposal_date` are mechanical.

**Always append exactly one final line per run**, even if nothing else
happened:
```json
{"date": "YYYY-MM-DD", "timestamp": "HH:mm:ss", "stage": "cycle_summary", "mode": "dry_run|live", "candidates_considered": N, "orders_reviewed": N, "orders_placed": N}
```
Load-bearing — Step 0's dry-run cycle count depends on this line existing
every run, keyed off `"date"` (distinct dates), not `"timestamp"`.

**After appending, regenerate `trade_log_recent.md`** (full overwrite,
not append) — a short, plain-English recap of today's cycle for a quick
mobile/GitHub read, not another machine format. A `# YYYY-MM-DD`
heading, then prose/bullet sections covering only what actually
happened this cycle (skip anything empty): the loss-limit check result;
each held position's stop-loss/take-profit status (symbol, `stop_pct`
used, gain/drawdown, and whether it triggered, fired a tier, sold, or
is just holding); each new-entry/top-up candidate considered and its
outcome in one line (approved and sized, or rejected and why); and any
orders actually placed (symbol, buy/sell, dollar amount). This is a
readable render of what this cycle already decided — no new research,
no re-deciding anything. Convenience view only, not a second audit
trail: `trade_log.jsonl` is still the source of truth, and if the two
ever disagree, trust `trade_log.jsonl`.

## Hard rules

- Never change `execution.mode` or any `risk_rules.json` value.
- Never call `place_equity_order` unless the live-order gate (Step 5 for
  sells, Step 6 for buys — same conditions) is open at that moment.
- A "high conviction" thesis never overrides a failed mechanical check.
- If required data can't be retrieved (portfolio, positions, P&L history),
  fail safe — treat the check as failed/halt new entries — and log exactly
  what failed.
- The wash-sale guard (Step 6's buy gate) only ever blocks a buy. It
  must never block, delay, or resize a
  stop_loss/take_profit/conviction_trim/exit_existing sell — a tax
  outcome never overrides risk management.
