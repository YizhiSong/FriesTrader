# Phase B — Re-verify, Risk Enforcement, Order Review/Execution, and Logging (Automated Daily Task)

This is the automated, unattended second half of this pipeline (see
`README.md` for the phase-split overview), run every weekday at 8:35am
Central (5 min after the 9:30am ET open) as a cloud routine. It performs
**Steps 4–7** of the
playbook (matching the master playbook's numbering), consuming candidates
produced by Phase A in `pending_proposals.jsonl`.

This task is authorized to place real live orders under a narrow, explicit
condition (see "Live-order gate" in Step 6 below). **That authorization
must be given explicitly, in advance, by whoever operates this pipeline —
after being warned that an unattended scheduled task has no human
confirmation at the moment of execution.** Do not add, remove, or loosen
any condition of that gate on your own judgment.

## Step 0 — Load state (do this first, every run)

1. Read `risk_rules.json` **fresh** — never assume prior values, never cache
   across runs. Use `account_number` from this file, not a hardcoded value.
2. Determine today's actual day of week mechanically (e.g. via
   `TZ='America/Chicago' date +'%A'`) — do not infer weekday from the date
   string yourself. This matters for the Step 4 weekend-gap check below.
3. Read `pending_proposals.jsonl`. As of the Phase A update, this file is
   overwritten each Phase A run and holds only the most recent run's
   entries — use its `"stage": "thesis"` entries directly as today's
   candidates (no need to hunt for "the most recent date" across multiple
   dates anymore; there should only be one). If the file doesn't exist or
   has no thesis entries, append one `cycle_summary` line (see Step 7)
   noting nothing to process, and stop — do not error out.
4. Read `trade_log.jsonl` (if present):
   - **Idempotency — key off the proposal's own date, not today's.** Every
     candidate from `pending_proposals.jsonl` carries its own `"date"`
     field (when Phase A wrote that thesis). For each candidate symbol from
     step 3, skip it if `trade_log.jsonl` already has a `risk_check` or
     `order` stage entry for that **same symbol with a matching
     `proposal_date` field** (see Step 7 — every risk_check/order entry
     must carry `proposal_date`, copied from the candidate's own `date` in
     `pending_proposals.jsonl`). Do **not** compare against today's
     execution date, and do not compare against the top-level `"date"`
     field on trade_log entries (that always reflects the day the decision
     was *made*, which changes daily even for the exact same stale
     proposal). This matters because Phase A overwrites
     `pending_proposals.jsonl` on its own daily schedule — if it ever fails
     to run on a given day, the same un-refreshed proposal would otherwise
     look "new" to every subsequent Phase B run (since the execution date
     keeps advancing) and get re-evaluated, and potentially re-approved and
     re-bought, every single day until Phase A finally refreshes it.
     Keying off `proposal_date` instead means a stale proposal only ever
     gets decided once, no matter how many days it sits un-refreshed.
     `stop_loss` and `take_profit` entries aren't proposal-derived and are
     exempt from this — they should always run fresh every day regardless.
   - **Dry-run cycle count**: count the number of **distinct calendar
     dates** (the top-level `"date"` field) that have at least one
     `"stage": "cycle_summary"` entry with `"mode": "dry_run"` —
     **not** the raw count of `cycle_summary` entries. If Phase B ever runs
     more than once on the same date (manual testing, a retry, etc.), that
     date still only counts once. This number is meant to represent days of
     validated real-world behavior, not how many times the task happened to
     execute — those aren't the same thing, and conflating them would let
     repeated same-day runs reach the live-order threshold far faster than
     intended. This is the authoritative count of completed dry-run cycles
     — it must be `>= risk_rules.json.execution.dry_run_min_cycles_before_live`
     before the live-order gate (Step 6) can ever open.

## Step 4 — Re-verify proposals against fresh opening data

**Resolve sells and classify candidates (do this first):**
1. Run the Step 5 stop-loss and take-profit checks now (see below for the
   mechanics) — pull `get_equity_positions` and fresh quotes, and resolve
   any triggered sells. It has to happen before anything else this cycle
   depends on knowing current slot occupancy.
2. Process any `direction: "exit_existing"` candidates from Step 0 now too
   (through the price-staleness check below, then straight to Step 6 as a
   sell) — selling is never gated, so these always go through regardless
   of how full the account is.
3. Split today's remaining `direction: "long"` candidates into two groups:
   - **new**: symbol is not currently a live open position — a genuine new
     entry, and the only kind of candidate that consumes an open slot.
   - **held**: symbol is already a live open position — a potential
     top-up (see Step 5). Top-ups never consume or need a slot, and are
     **always** considered every cycle regardless of how full the account
     is — buying more of something you already hold is not gated behind
     "no room for anything else."
4. Compute `open_slots = max_concurrent_positions - (live positions per
   get_equity_positions, excluding any just resolved to sell in 1–2 above)`.
   Only the **new** group can ever consume a slot, and top-ups can't create
   one either, so this number is fixed for the rest of the cycle unless a
   **new**-group candidate gets approved in Step 5.
5. **If `open_slots <= 0`**: no **new**-group candidate can be approved
   this cycle no matter its merit. Skip the weekend-gap search, the
   price-staleness re-check, and Step 5's per-candidate work for every
   candidate in the **new** group only — don't spend API calls or search
   budget on candidates that can't be acted on either way. Log one
   lightweight line per skipped new-group candidate instead:
   `"stage": "risk_check", "passed": false, "proposal_date": "<candidate's date from pending_proposals.jsonl>", "reason": "no open slots this cycle (X of Y max already held/approved) — skipped without staleness re-check"`.
   The **held** group is unaffected by this — continue to the normal flow
   below for it regardless.
6. **If `open_slots > 0`**: the **new** group also continues to the normal
   flow below (slots may still run out partway through Step 5 if enough
   new entries get approved ahead of a given candidate in priority order —
   that's an ordinary per-candidate concurrency check, not this upfront
   short-circuit).

For every **new**-group candidate not already short-circuited by 5, and
every **held**-group candidate (always, regardless of open slots):

### Weekend gap (Monday runs only)

A Friday-afternoon proposal is stale in a way an overnight one isn't — 2.5
days pass with zero screening, not ~16 hours. **If today is Monday** (per
Step 0.2):

1. Before the price-based staleness check below, run **one additional
   targeted web search per pending proposal** covering Saturday/Sunday
   (earnings surprises, M&A, guidance changes, major macro events). This
   is in addition to, not a replacement for, the normal
   `cadence.news_search_budget_per_cycle` cap from `risk_rules.json` —
   these Monday searches are a separate, small budget of exactly one per
   proposal, not subject to that cap.
2. If anything materially contradicts the original thesis or invalidation
   criteria from Phase A, drop the proposal — log
   `"stage": "risk_check", "passed": false, "proposal_date": "<candidate's date from pending_proposals.jsonl>", "reason": "weekend news invalidated thesis: <what you found>"`
   — and do not process it further in Steps 5–6.
3. If nothing turns up, proceed to the price-based check below as usual.

On any other weekday, skip straight to the price-based check.

### Price-based staleness check (every day)

Pull a fresh quote via `get_equity_quotes` for the candidate — re-verify
against this morning's opening price, do not reuse Phase A's price from
the prior close. If the price gapped significantly (e.g. large overnight
or weekend move), treat the original thesis as potentially stale rather
than assuming it still holds — re-check it against the thesis's own
`invalidation` criteria from Phase A. If the gap plausibly invalidates the
thesis, drop it the same way as the weekend-gap check above.

## Step 5 — Mechanical risk enforcement

**Stop-loss check (always runs, independent of new candidates):**
Pull current `get_equity_positions` and fresh `get_equity_quotes` for every
open position. For each, compute drawdown from average cost. If it meets or
exceeds `stop_loss.hard_stop_pct`, this is an immediate full-position sell —
no thesis review needed or allowed, and it is never blocked by a loss-limit
halt (it's an exit, not a new entry). Log `"stage": "stop_loss"`. If
triggered, treat it as a candidate for Step 6 (sell, side=sell). A good
thesis never cancels a stop-loss — see `risk_rules.json`'s own note on this.

**Take-profit check (always runs, independent of new candidates):**
Using the same `get_equity_positions`/quotes pull as the stop-loss check
above (no need to call again), compute each open position's gain from
average cost. If it meets or exceeds `take_profit.target_pct`, this is an
immediate full-position sell — no thesis review needed or allowed, and it
is never blocked by a loss-limit halt (it's an exit, not a new entry). Log
`"stage": "take_profit"`. If triggered, treat it as a candidate for Step 6
(sell, side=sell). A good thesis never cancels a take-profit either — this
account is short-term oriented, not buy-and-hold — see `risk_rules.json`'s
own note on this.

**Stop-loss re-entry lock — price-gated, not time-gated**: check
`trade_log.jsonl` for this symbol's most recent `"stage": "order"` entry
with `"reason": "stop_loss"` (a stop-out sell). If one exists and no
later `order` entry for that symbol shows it was bought since, the symbol
is locked out of any new buy (new entry or top-up) — including later in
this same cycle — until a fresh quote is **at or below** the price it was
sold at (that entry's `quote_bid`), no matter how many cycles or days
have passed, and regardless of thesis quality or conviction. This exists
specifically to prevent selling at a loss and then buying back at a
higher price. Before ranking, pull a fresh quote for any candidate with
an unresolved stop-loss lock and drop it from the merged priority order
below if the fresh price is above that stop-out price — log it as its
own line rather than silently omitting it:
`"stage": "risk_check", "passed": false, "proposal_date": "<candidate's date from pending_proposals.jsonl>", "reason": "stop-loss re-entry lock — current price <X> is above the <Y> it was stopped out at on <date>"`.
Once the fresh price is at or below the stop-out price, the lock clears
and it's eligible again as a normal candidate through Phase A's usual
screening — no separate time-based cooldown on top of this.

**Loss-limit halt check (always runs, gates all new entries and top-ups):**
Determine today's and this week's account P&L as a percentage of account
value, using `get_pnl_trade_history` / `get_realized_pnl` and
`get_portfolio`, compared against `starting_capital_usd` in
`risk_rules.json`. If either the daily or weekly drawdown meets or exceeds
`loss_limits.daily_loss_limit_pct_of_account` /
`loss_limits.weekly_loss_limit_pct_of_account`, set `entries_halted = true`
for this run. **If the P&L data cannot be determined cleanly for any
reason, fail safe: treat it as breached and set `entries_halted = true`.**
This halts **both** new entries and top-ups — a top-up still increases
exposure and spends cash, so it's treated the same as a new entry here,
even though it's exempt from the concurrency/slot check. Log this
determination as its own line, `"stage": "loss_limit_check"`.

**Candidate priority order — new entries and top-ups compete on equal
footing (decide this before running any per-candidate check):**
Merge the **new** and **held** groups from Step 4 — excluding any
new-group candidates already rejected by the Step 4 capacity short-circuit,
since those are already logged and done — into a **single combined list**,
sorted:
1. **Conviction tier first**: `high` before `medium` before `low`.
2. **Within the same tier**, break ties by `pct_below_52wk_high` from the
   thesis record, **descending** (a candidate trading further below its own
   52-week high is prioritized — this is a disclosed proxy for "room in the
   setup," not a rigorous fair-value calculation; treat a missing/absent
   field as the lowest priority within its tier rather than erroring).
Process **all** of them — new-entry candidates and top-up candidates
together — strictly in this one merged order. A high-conviction top-up can
be evaluated, and approved, ahead of a lower-conviction new entry, and vice
versa: the two compete purely on thesis quality, not on which group they
started in. Track two running totals as you go through this order, since
both groups draw from the same account:
- `cash_remaining`, decremented by every approved candidate regardless of
  group (new entries and top-ups both spend cash).
- `concurrent_positions_after`, incremented **only** by approved
  **new**-group candidates (top-ups never touch this — they don't occupy
  an additional slot).
**Log `pct_below_52wk_high` as a real structured field on every single
risk_check entry produced from this sort — winners and rejections alike —
not just mentioned in prose for whichever candidates happened to pass.**
`pending_proposals.jsonl` gets overwritten daily, so `trade_log.jsonl` is
the only place this number survives for later audit. When a **new**-group
candidate is rejected purely because slots ran out, log the reason as
`"concurrent_positions_after (N) exceeds max_concurrent_positions (M) — cap filled by higher-priority candidates this cycle"`
so it's clear this wasn't a quality rejection, just a scarcity one.

**Per-candidate checks**, for each candidate in the merged priority order:

*If it's a **new**-group candidate:*
1. Compute proposed position size from the thesis's `conviction`, using
   this fixed, deterministic table (not runtime judgment):
   - `high` → `max_position_pct_of_account` (i.e. the full cap, currently 0.20)
   - `medium` → 0.12
   - `low` → 0.06
   Dollar amount = that percentage × live `total_value`, rounded to 2 decimals.
2. If `entries_halted` is true, reject: `"stage": "risk_check", "passed": false, "reason": "loss limit halt — action_on_limit_hit"`. No exception for high conviction.
3. Check, using the running totals above:
   - position size ≤ `max_position_pct_of_account`
   - `concurrent_positions_after` (running count + 1 for this candidate) ≤ `max_concurrent_positions`
   - `cash_remaining` after this trade ≥ `min_cash_buffer_pct` × `total_value`
4. Pass → increment both running totals, log
   `"stage": "risk_check", "passed": true"` with the computed numbers.
   Fail → reject, log `"stage": "risk_check", "passed": false"` with the
   specific reason; running totals unchanged.

*If it's a **held**-group candidate (a possible top-up):*
1. If `entries_halted` is true, reject the same way as a new entry —
   `"stage": "risk_check", "passed": false, "position_action": "top_up", "reason": "loss limit halt — action_on_limit_hit"`.
2. `target_size = ` the same conviction-tier percentage × live
   `total_value` (`high`→0.20, `medium`→0.12, `low`→0.06) — the position's
   target size overall, not an amount to add on top of what's already held.
3. Compute the position's current market value: quantity (from
   `get_equity_positions`) × fresh price (from `get_equity_quotes`).
4. `headroom = target_size - current_position_value`. **If `headroom <= 0`**
   (already at or above its own conviction tier's target), reject —
   `"stage": "risk_check", "passed": false, "position_action": "top_up", "reason": "already at or above target size for its conviction tier — no top-up"`.
   A thesis reconfirmed unchanged does **not** by itself justify repeated
   buying; this check is what prevents that.
5. **If `headroom > 0`**: the top-up amount is
   `min(headroom, max_position_pct_of_account × total_value − current_position_value)`
   — that second term is a hard ceiling so a top-up can never push total
   exposure to this symbol past `max_position_pct_of_account`, no matter
   the conviction tier. No concurrency check — top-ups never touch
   `concurrent_positions_after`.
6. Check `cash_remaining` after this trade ≥ `min_cash_buffer_pct` ×
   `total_value`, using the running total shared with new entries. Fail →
   reject and log why; running total unchanged.
7. Pass → decrement `cash_remaining`, log
   `"stage": "risk_check", "passed": true, "position_action": "top_up"`
   with the computed numbers (current value, target, headroom, buy amount).

Every `risk_check` entry from either branch must include `proposal_date`
(copied from this candidate's `"date"` in `pending_proposals.jsonl` — used
by Step 0's idempotency check) and, for `direction: "long"` candidates,
`pct_below_52wk_high` (copied from the thesis record — used to audit the
priority sort later). Top-up entries must additionally include
`"position_action": "top_up"` so they're distinguishable from new entries.

Candidates with `direction: "avoid"` are not processed further (already
logged as such in Phase A). Candidates with `direction: "exit_existing"`
for a symbol currently held skip the checks above entirely (selling
reduces risk, so it isn't blocked by `max_position_pct`, concurrency, cash
buffer, or the loss-limit halt) and go straight to Step 6 as a sell.

## Step 6 — Dry run before anything live (order review and the live-order gate)

For every candidate that passed Step 5 (including stop-loss, take-profit,
and exit_existing sells, and any approved position top-ups):

1. **Always** call `review_equity_order` first — this is a preview and
   never places anything by itself.
2. If `review_equity_order` surfaces any blocking alert, do not proceed to
   placement regardless of mode; log the alert verbatim and treat as
   rejected.
3. Otherwise, branch on `execution.mode` (read fresh in Step 0) and the
   dry-run cycle count (from Step 0):

   **Live-order gate — ALL of these must be true simultaneously:**
   - `execution.mode == "live"`
   - dry-run cycle count `>= execution.dry_run_min_cycles_before_live`
   - `review_equity_order` for this exact order returned no blocking alert

   - If the gate is **open**: call `place_equity_order` with the exact
     parameters just reviewed. Log `"stage": "order", "mode": "live", "placed": true"`
     plus the fill/confirmation details returned.
   - If `execution.mode == "dry_run"`: log
     `"stage": "order", "mode": "dry_run", "would_execute": true"` and stop.
     **Never call `place_equity_order` in this branch.**
   - If `execution.mode == "live"` but the cycle count is still under the
     threshold: do **not** place. Log
     `"stage": "order", "mode": "live_blocked_insufficient_cycles", "would_execute": true, "placed": false"`
     with the current vs. required cycle count.

Never change `execution.mode` yourself, under any branch. Never invent or
guess a field value — if a required tool call fails, log the failure for
that candidate and skip it rather than guessing. Every `order` entry from
this step must also carry `proposal_date` (same as Step 5), since Step 0's
idempotency check matches against either a `risk_check` or an `order` entry.

## Step 7 — Logging

Append every decision from this run to `trade_log.jsonl` — one JSON line
each: `stop_loss`, `take_profit`, `loss_limit_check`, `risk_check` (pass and fail,
including weekend-gap and price-gap rejections from Step 4, and position
top-up evaluations from Step 5), and `order` stages, in the same shape
already used in `trade_log.jsonl` / `trade_log_template.jsonl`. Any entry
related to a position top-up (rather than a brand-new entry) must include
`"position_action": "top_up"` so it's distinguishable in the log.

**Every single line this step writes — including the final `cycle_summary`
— must include a `"timestamp"` field**: the actual real-world time this
run started (e.g. via `TZ='America/Chicago' date +'%H:%M:%S'`), never
guessed or hallucinated.
`timestamp` is time-of-day only (`HH:mm:ss`, e.g. `"08:35:01"`) — do not
prepend or duplicate the date into it. This is purely for human readability
when scanning the log (e.g. telling apart multiple same-day runs) — it is a
separate field from `"date"` and `"proposal_date"`, and must **never** be
used for idempotency, the dry-run cycle count, or any other logic. Those
two fields (`date` and `proposal_date`) remain the only ones anything
mechanical keys off.

**Always append exactly one final line per run**, even if nothing else
happened:
```json
{"date": "YYYY-MM-DD", "timestamp": "HH:mm:ss", "stage": "cycle_summary", "mode": "dry_run|live", "candidates_considered": N, "orders_reviewed": N, "orders_placed": N}
```
This line is load-bearing — Step 0's dry-run cycle count depends on it
existing every run, keyed off its `"date"` field (distinct dates, per the
counting rule above), not its `"timestamp"`.

## Hard rules

- Never change `execution.mode` or any value in `risk_rules.json`.
- Never call `place_equity_order` unless the full live-order gate in Step 6
  is open at the moment of the call.
- A "high conviction" thesis never overrides a failed mechanical check.
- If required data can't be retrieved (portfolio, positions, P&L history),
  fail safe — treat the relevant check as failed / halt new entries — and
  log exactly what failed, rather than guessing or filling in a placeholder.
