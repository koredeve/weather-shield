# WeatherShield

Parametric weather insurance on GenLayer. A holder buys a policy for a location and peril
(rain, wind, snow, or low temperature) by paying a premium equal to coverage/10. Anyone can
trigger a weather check: the contract fetches a live weather API, compares the observed value
against the policy threshold, and either credits the coverage to the holder (`paid`) or closes
the policy with no payout (`denied`). Credits are withdrawable at any time.

## Architecture

- **User action**: `buy_policy` (payable) locks the premium; `check_weather` can be called by
  anyone on an active policy; `withdraw` pays out accumulated credits.
- **Evidence source**: a live weather HTTP API (`base_url` configured by the owner) that returns
  `{"value": <number>}` — mm of rain, km/h of wind, cm of snow, or degrees Celsius.
- **Nondet call**: `check_weather` runs a leader function that performs
  `gl.nondet.web.get(base_url + "?location=...&peril=...")`, parses the stable `value` field,
  converts it to an integer `value_x10` (value * 10, rounded) inside the nondet closure, and
  evaluates the trigger: `value_x10 >= threshold_x10` for rain/wind/snow, `value_x10 <= threshold_x10`
  for `temp_low_c`.
- **Equivalence principle**: the validator independently reruns the leader fetch and agrees only if
  the **triggered boolean is exactly equal** and the two `value_x10` readings are **within ±2**
  (0.2 units) of each other. Leader errors are reconciled with `_handle_leader_error`:
  `[EXTERNAL]`/`[EXPECTED]` must match verbatim, `[TRANSIENT]` matches by class.
- **Settlement effect**: on agreement the policy becomes `paid` (coverage credited to the holder)
  or `denied`; the observed `last_value_x10` is persisted on the policy either way.
- **Appeal path**: GenLayer Optimistic Democracy natively provides leader-proposes /
  validator-check with an appeal window, so a disputed check can be re-run by a larger validator
  set before finalization.

## Quickstart

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# lint the contract
/Users/mac/Documents/Default\ Project/.venv/bin/genvm-lint check contracts/WeatherShield.py --json

# direct-mode tests (leader path, mocked web)
pytest tests/direct/ -v
```

## Interface

| Method | Type | Notes |
| --- | --- | --- |
| `owner()` | view | Deployer address |
| `get_base_url()` | view | Configured weather API endpoint |
| `get_policy(policy_id)` | view | Full policy record (`holder` as string, `last_value_x10` may be negative for temperatures) |
| `credit_of(who)` | view | Withdrawable credit for an address |
| `total_policies()` | view | Number of policies ever created |
| `set_base_url(url)` | write | Owner-only, sets the weather API base URL |
| `buy_policy(policy_id, location, peril, threshold_x10, coverage_atto)` | write, payable | `peril` in `rain_mm`/`wind_kmh`/`snow_cm`/`temp_low_c`; must attach exactly `coverage_atto // 10`; unique id |
| `check_weather(policy_id)` | write | Anyone; active policies only; resolves to `paid`/`denied` |
| `withdraw()` | write | Transfers caller's credit out, zeroes the record |

## StudioNet

Deploy and try it on StudioNet: gasless transactions, 0 GEN needed — just select `studionet` in
the config (`gltest.config.yaml` already defaults to it).
