# Phase B — Re-verify, Risk Enforcement, Order Review/Execution, and Logging (Automated Daily Task)

Automated second half of this pipeline (see `README.md`), run every
weekday 8:35am Central (5 min after 9:30am ET open) as a cloud routine.
Performs **Steps 4–7**, consuming candidates from Phase A's
`pending_proposals.jsonl`.

Authorized to place real live orders under a narrow condition (see Step
6's "Live-order gate"). **That authorization must be given explicitly, in
advance, by whoever operates this pipeline — after being warned that an
unattended scheduled task has no human confirmation at the moment of
execution.** Do not add, remove, or loosen any gate condition on your own
judgment.

## Step 0 — Load state (do this first, every run)

1. Read `risk_rules.json` **fresh** — never cache across runs. Use its
   `account_number`, not a hardcoded value.
2. Determine today's day of week mechanically (e.g.
   `TZ='America/Chicago' date +'%A'`) — don't infer it from the date
   string. Needed for Step 4's weekend-gap check.
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
     `>= execution.dry_run_min_cycles_before_live` before Step 6's
     live-order gate can open.

## Step 4 — Re-verify proposals against fresh opening data

**Resolve sells and classify candidates (do this first):**
1. Run Step 5's stop-loss and take-profit checks now (pull
   `get_equity_positions` and fresh quotes, resolve any triggered sells) —
   needed before knowing current slot occupancy.
2. Process any `direction: "exit_existing"` candidates now too (through
   the staleness check below, then to Step 6 as a sell) — selling is
   never gated.
3. Split remaining `direction: "long"` candidates:
   - **new**: not a live open position — a genuine new entry, the only
     kind that consumes a slot.
   - **held**: already a live open position — a potential top-up (Step 5).
     Top-ups never consume a slot and are always considered regardless of
     account fullness.
4. `open_slots = max_concurrent_positions - (live positions per
   get_equity_positions, excluding sells just resolved)`. Only **new**
   candidates consume a slot; fixed for the cycle unless one gets
   approved in Step 5.
5. **If `open_slots <= 0`**: no **new** candidate can be approved this
   cycle. Skip the weekend-gap search, staleness check, and Step 5 work
   for every **new**-group candidate — log instead:
   `"stage": "risk_check", "passed": false, "proposal_date": "<candidate's date from pending_proposals.jsonl>", "reason": "no open slots this cycle (X of Y max already held/approved) — skipped without staleness re-check"`.
   **held** group is unaffected.
6. **If `open_slots > 0`**: **new** group continues normally (slots may
   still run out mid-Step-5 via ordinary per-candidate concurrency check).

For every **new** candidate not short-circuited by 5, and every **held**
candidate (always):

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

### Price-based staleness check (every day)

Pull a fresh quote (`get_equity_quotes`) — re-verify against this
morning's open, not Phase A's prior-close price. If the price gapped
significantly, re-check against the thesis's `invalidation` criteria —
same sourcing rules as above, cite whatever explains the gap in a
`"sources"` field on the resulting log line; if the gap plausibly
invalidates it, drop it as above.

## Step 5 — Mechanical risk enforcement

**Stop-loss check (always runs, independent of new candidates):**
Pull current `get_equity_positions` and fresh `get_equity_quotes` for
every open position; compute drawdown from average cost. If it meets or
exceeds `stop_loss.hard_stop_pct`, immediate full-position sell — no
thesis review, never blocked by a loss-limit halt (it's an exit). Log
`"stage": "stop_loss"`; if triggered, treat as a Step 6 sell candidate. A
good thesis never cancels a stop-loss — see `risk_rules.json`'s note.

**Take-profit check (always runs, independent of new candidates, tiered
partial sells)**: Using the same pull as the stop-loss check (no need to
call again), compute each position's gain from average cost. For each
`take_profit.tiers` entry, in ascending `gain_pct` order: check
`trade_log.jsonl` for a `"stage": "take_profit"` entry for this symbol at
that exact `gain_pct` tier, logged since the position's quantity last
reached zero (a full exit) — if found, this tier already fired, skip it.
Otherwise, if today's gain meets or exceeds that tier's `gain_pct`, fire
it: sell `sell_fraction_of_position` of the position's **quantity as it
stands at this moment** (i.e. after any earlier tier that already fired
this same cycle has reduced it). No thesis review, never blocked by a
loss-limit halt (it's an exit, not a new entry). Log
`"stage": "take_profit", "tier_gain_pct": <the tier fired>, "sell_fraction": <fraction>, "quantity_before": <N>, "quantity_sold": <N>, "triggered": true, "action": "sell_partial_position"`
and treat it as its own Step 6 sell candidate. **If a single cycle's gain
has jumped past more than one not-yet-fired tier at once, fire all of
them in ascending order within that cycle** — don't skip a lower tier
just because a higher one was also reached. If no tier fires this cycle,
log one line noting each tier's fired/not-fired status,
`"triggered": false, "action": "hold_monitor"`. Once all three tiers have
fired, the remaining quantity is held long indefinitely — only the
stop-loss check above still applies to it. Tiers become eligible again
only after the position is fully closed to zero shares and a new entry
is later opened (a genuinely new holding period, not a top-up).

**No same-cycle sell-then-buy**: if a symbol's stop-loss fired or any
take-profit tier fired earlier in this same cycle, it is not eligible
for a top-up this same cycle, regardless of thesis or conviction — drop
it from the **held** group before the merged priority order below,
logging
`"stage": "risk_check", "passed": false, "position_action": "top_up", "reason": "stop-loss/take-profit fired this cycle — not eligible for a same-cycle top-up"`.
This applies unconditionally (dry_run or live) since it's about not
producing a self-contradictory sell-and-buy decision within one cycle,
not about whether the sell actually executed. It's a normal top-up
candidate again starting next cycle (subject to the sell re-entry lock
below).

**Sell re-entry lock — price-gated, not time-gated, any sell type**:
check `trade_log.jsonl` for this symbol's most recent `"stage": "order"`
entry whose `reason` is any sell (`stop_loss`, `take_profit`, or
`exit_existing`). This lock only applies if that sell actually
executed — confirm via `get_equity_positions` that its quantity is
genuinely lower than it was immediately before that logged sell (or the
position was fully closed and re-opened since). A `dry_run` sell entry
never actually reduces the position, so if `get_equity_positions` still
shows the same (or higher) quantity as before that logged sell, there
was no real reduction from it and the lock does not apply — the symbol
should be evaluated normally (e.g. a top-up), not dropped. When the sell
did actually execute and no later `order` entry for that symbol shows a
buy since, the symbol is locked out of any new buy (new entry or
top-up) — including later in this same cycle — until a fresh quote is
**at or below** the price it was sold at (that entry's `quote_bid`), no
matter how many cycles or days have passed, and regardless of thesis
quality or conviction. This exists to prevent buying back into a symbol
at a worse price than you just sold it at, whatever the reason for that
sell — averaging up right after trimming or exiting undermines the
whole point of it. Before ranking, pull a fresh quote for any candidate
with an unresolved (actually-executed) sell lock and drop it from the
merged priority order below if the fresh price is above that sell
price — log it as its own line rather than silently omitting it:
`"stage": "risk_check", "passed": false, "proposal_date": "<candidate's date from pending_proposals.jsonl>", "reason": "sell re-entry lock — current price <X> is above the <Y> it was sold at on <date> (reason: <stop_loss|take_profit|exit_existing>)"`.
Once the fresh price is at or below that sell price, the lock clears and
it's eligible again as a normal candidate through Phase A's usual
screening — no separate time-based cooldown on top of this.

**Loss-limit halt check (always runs, gates all new entries and top-ups):**
Determine today's and this week's account P&L as % of account value
(`get_pnl_trade_history`/`get_realized_pnl` and `get_portfolio`, vs
`starting_capital_usd`). If daily or weekly drawdown meets/exceeds
`loss_limits.daily_loss_limit_pct_of_account`/`weekly_loss_limit_pct_of_account`,
set `entries_halted = true`. **If P&L can't be determined cleanly, fail
safe: treat as breached.** Halts both new entries and top-ups (a top-up
still spends cash/exposure, even though it skips the concurrency check).
Log as `"stage": "loss_limit_check"`.

**Candidate priority order — new entries and top-ups compete equally
(decide before any per-candidate check):**
Merge **new** and **held** groups from Step 4 (excluding new-group
candidates already rejected by Step 4's capacity short-circuit) into one
list, sorted:
1. **Conviction tier first**: `high` before `medium` before `low`.
2. **Within a tier**, break ties by `pct_below_52wk_high` **descending**
   (further below its own 52-week high is prioritized — a disclosed "room
   in the setup" proxy, not a fair-value calc; missing field = lowest
   priority in its tier).
Process all of them — new entries and top-ups together — strictly in this
merged order; a high-conviction top-up can be approved ahead of a
lower-conviction new entry and vice versa. Track two running totals:
- `cash_remaining`, decremented by every approved candidate (both groups
  spend cash).
- `concurrent_positions_after`, incremented **only** by approved
  **new**-group candidates.
**Log `pct_below_52wk_high` as a structured field on every risk_check
entry from this sort — winners and rejections alike** (the only place this
survives, since `pending_proposals.jsonl` is overwritten daily). A
**new**-group candidate rejected purely for lack of slots: log
`"concurrent_positions_after (N) exceeds max_concurrent_positions (M) — cap filled by higher-priority candidates this cycle"`
to show it's scarcity, not quality.

**Per-candidate checks**, for each candidate in the merged priority order:

*If it's a **new**-group candidate:*
1. Position size from `conviction`, fixed table (not runtime judgment):
   - `high` → `max_position_pct_of_account` (currently 0.20)
   - `medium` → 0.12
   - `low` → 0.06
   Dollar amount = percentage × live `total_value`, rounded to 2 decimals.
2. If `entries_halted`, reject: `"stage": "risk_check", "passed": false, "reason": "loss limit halt — action_on_limit_hit"`. No high-conviction exception.
3. Check, using running totals:
   - position size ≤ `max_position_pct_of_account`
   - `concurrent_positions_after` (running count + 1) ≤ `max_concurrent_positions`
   - `cash_remaining` after trade ≥ `min_cash_buffer_pct` × `total_value`
4. Pass → increment both totals, log
   `"stage": "risk_check", "passed": true"` with computed numbers. Fail →
   reject, log `"stage": "risk_check", "passed": false"` with the reason;
   totals unchanged.

*If it's a **held**-group candidate (a possible top-up):*
1. If `entries_halted`, reject: `"stage": "risk_check", "passed": false, "position_action": "top_up", "reason": "loss limit halt — action_on_limit_hit"`.
2. `target_size` = same conviction-tier % × live `total_value`
   (`high`→0.20, `medium`→0.12, `low`→0.06) — the target size overall, not
   an add-on amount.
3. Current market value = quantity (`get_equity_positions`) × fresh price
   (`get_equity_quotes`).
4. `headroom = target_size - current_position_value`. **If `headroom <= 0`**,
   reject — `"stage": "risk_check", "passed": false, "position_action": "top_up", "reason": "already at or above target size for its conviction tier — no top-up"`.
   An unchanged thesis alone doesn't justify repeated buying.
5. **If `headroom > 0`**: top-up amount =
   `min(headroom, max_position_pct_of_account × total_value − current_position_value)`
   — the second term is a hard ceiling on total exposure regardless of
   conviction. No concurrency check.
6. Check `cash_remaining` after trade ≥ `min_cash_buffer_pct` ×
   `total_value` (shared running total). Fail → reject and log why; total
   unchanged.
7. Pass → decrement `cash_remaining`, log
   `"stage": "risk_check", "passed": true, "position_action": "top_up"`
   with current value, target, headroom, buy amount.

Every `risk_check` entry must include `proposal_date` (copied from the
candidate's `"date"` in `pending_proposals.jsonl` — Step 0's idempotency
key) and, for `direction: "long"`, `pct_below_52wk_high` (for auditing the
priority sort). Top-up entries must also include
`"position_action": "top_up"`.

`direction: "avoid"` candidates aren't processed further (already logged
in Phase A). `direction: "exit_existing"` candidates for a held symbol
skip all the checks above (selling reduces risk — not blocked by
position/concurrency/cash-buffer/loss-limit checks) and go straight to
Step 6 as a sell.

## Step 6 — Dry run before anything live (order review and the live-order gate)

For every candidate that passed Step 5 (stop-loss, take-profit,
exit_existing sells, and approved top-ups):

1. **Always** call `review_equity_order` first — a preview, never places
   anything.
2. If it surfaces a blocking alert, do not proceed to placement regardless
   of mode; log the alert verbatim and treat as rejected.
3. Otherwise, branch on `execution.mode` (fresh from Step 0) and the
   dry-run cycle count:

   **Live-order gate — ALL must be true:**
   - `execution.mode == "live"`
   - dry-run cycle count `>= execution.dry_run_min_cycles_before_live`
   - `review_equity_order` for this order returned no blocking alert

   - **Gate open**: call `place_equity_order` with the reviewed
     parameters. Log `"stage": "order", "mode": "live", "placed": true"`
     plus fill/confirmation details.
   - `execution.mode == "dry_run"`: log
     `"stage": "order", "mode": "dry_run", "would_execute": true"` and stop.
     **Never call `place_equity_order` here.**
   - `execution.mode == "live"` but cycle count still under threshold: do
     **not** place. Log
     `"stage": "order", "mode": "live_blocked_insufficient_cycles", "would_execute": true, "placed": false"`
     with current vs. required count.

Never change `execution.mode` yourself. Never invent/guess a field value —
if a tool call fails, log the failure and skip that candidate. Every
`order` entry must carry `proposal_date` (same as Step 5) — Step 0's
idempotency check matches against either a `risk_check` or `order` entry.

## Step 7 — Logging

Append every decision to `trade_log.jsonl` — one JSON line each:
`stop_loss`, `take_profit`, `loss_limit_check`, `risk_check` (pass/fail,
including Step 4's weekend-gap/price-gap rejections and Step 5's top-up
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

## Hard rules

- Never change `execution.mode` or any `risk_rules.json` value.
- Never call `place_equity_order` unless Step 6's live-order gate is open
  at that moment.
- A "high conviction" thesis never overrides a failed mechanical check.
- If required data can't be retrieved (portfolio, positions, P&L history),
  fail safe — treat the check as failed/halt new entries — and log exactly
  what failed.
