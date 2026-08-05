# FriesTrader

An agentic trading pipeline: Claude Code + a brokerage MCP connector,
running as two scheduled cloud sessions that screen, reason about, and
(under a narrow, explicit gate) execute real trades — with the actual
safety mechanism being mechanical, auditable risk rules, not the LLM's
judgment.

This is a template/framework extracted from a real, live deployment.
Adapt it, don't just run it blind — read "What this does and doesn't
solve" below before pointing it at real money.

## How it works

Trading runs as **two separate phases, on two separate schedules** — a
full trading day's closing data feeds the thesis, and a fresh opening
price is used for the actual order, rather than trading on a stale
overnight price.

- **Phase A** (Steps 1–3, ~4:30pm Central weekdays) — screens candidates,
  gathers signals, writes a logged thesis per candidate to
  `pending_proposals.jsonl`. Places no orders, not even dry-run ones.
  Full spec: `PHASE_A_TASK.md`.
- **Phase B** (Steps 4–7, ~8:35am Central weekdays) — re-verifies Phase A's
  proposals against fresh opening data, enforces `risk_rules.json`
  mechanically, and dry-runs or (gated) places orders. Full spec:
  `PHASE_B_TASK.md`.

Both are designed to run as cloud-hosted scheduled agent sessions,
independent of any local machine — each run clones this repo fresh and
commits/pushes its results back to `main`, so the repo itself is the
persistent state, not local disk.

## Files

- `risk_rules.json` — the hard, mechanical limits (position sizing, stop-
  loss, loss limits, universe filters, execution mode). Nothing in this
  system should be able to override these. **Fill in your own
  `account_number` before using this** — the placeholder here is not a
  real account. **Also set `universe.watchlist_name`** to the name of a
  watchlist you actually have in your brokerage account — Phase A pulls
  candidates from that list by name, not a hardcoded one. **Also fill in
  `wash_sale_avoidance.linked_accounts`** with every brokerage account
  number you personally control that might hold the same symbols (a
  separate personal account, an IRA, etc.) — the IRS wash-sale rule
  applies per taxpayer across all your accounts, not just this one, and
  this list is how the guard checks the others. Set
  `wash_sale_avoidance.enabled` to `false` if you'd rather not have buys
  blocked by this check at all.
- `PHASE_A_TASK.md` / `PHASE_B_TASK.md` — the full, self-contained spec
  each phase follows.
- `trade_log_template.jsonl` — the log line shapes; real logs should
  accumulate in a file like `trade_log.jsonl` in this same style.
  Phase B also regenerates `trade_log_recent.md` (full overwrite) — a
  plain-English recap of the latest day, for a quick mobile/GitHub read
  without parsing raw JSON — convenience view only, `trade_log.jsonl`
  is still the source of truth.

## Thesis record shape (Phase A, Step 3)

```json
{
  "symbol": "XXXX",
  "date": "YYYY-MM-DD",
  "thesis": "1-3 sentences on what changed and why it might matter",
  "conviction": "low | medium | high",
  "invalidation": "what would prove this thesis wrong",
  "direction": "long | avoid | exit_existing",
  "sources": ["Outlet Name: https://...", "..."]
}
```

- **No price targets** — no reliable basis for a specific number, and it
  invites false precision.
- **No forecasting language treated as fact** — "this suggests...", not
  "this will...".

## First-time setup

1. Fill in `account_number` in `risk_rules.json` with your own brokerage
   account number, set `universe.watchlist_name` to a watchlist you've
   already created and populated in your brokerage account, and review
   every other threshold — the defaults here are illustrative, not a
   recommendation.
2. Fill in `wash_sale_avoidance.linked_accounts` with every brokerage
   account number you personally control, not just this one — if this is
   genuinely the only account you trade in, a single-entry list (just
   this account's number) is enough. Leave `enabled: true` unless you
   specifically want buys never blocked on wash-sale grounds.
3. Keep `execution.mode` set to `"dry_run"`. Leave it there for at least
   the number of cycles set in `dry_run_min_cycles_before_live` — don't
   shortcut this.
4. After each cycle, read `trade_log.jsonl` yourself. Look specifically
   at rejected candidates and stop-loss triggers, not just the trades
   that "worked" — that's where you'll see if the reasoning step is
   actually sound or just getting lucky with an uptrend.
5. Only flip `execution.mode` to `"live"` yourself, by hand, after you've
   reviewed enough dry-run cycles to trust the output. Do not let the
   agent flip it for you as a shortcut.

## What this does and doesn't solve

- It gives you a structured, auditable version of "let an LLM screen and
  reason about trades" instead of an opaque one.
- It does **not** make LLM-driven stock picking more likely to beat a
  simple index fund — there's no established track record for that, and
  this can't backtest the reasoning step honestly (news-based reasoning
  can't be validated against historical data the model may already know
  the outcome of).
- The risk rules are the actual safety mechanism here, not the reasoning
  quality. Treat loosening them as the highest-risk change you can make
  to this system.
- This is a template extracted from a real deployment trading a small
  personal account, shared for others to learn from or adapt. It is
  genuinely not financial advice, and running it against real money is
  entirely your own decision and risk.

## License

MIT — see `LICENSE`. Provided as-is, with no warranty; see the license
for the full disclaimer.
