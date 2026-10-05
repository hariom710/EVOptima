# ML findings — EV charging prediction

What was wrong with the shipped model, how it was diagnosed, and what replaced it.

**TL;DR:** the previously deployed model reported R² ≈ 1.00 from a random split over a
cumulative target. Under an honest time split it scored **−2.9**. The target is now the
*charging rate* rather than cumulative kWh, the split is chronological, calendar features
are removed wherever they only identify the dataset, and the model was retrained:

| model | algorithm | target | blocked CV R² | chrono 80/20 | latency | size |
|---|---|---|---|---|---|---|
| `power` | `XGBRegressor` | `Charging Power_kW` | **+0.9960** | +0.9960 / +0.9984 | 0.34 ms/row | 3.20 MB |

**0.34 ms/row and 3.20 MB**, against the previous RandomForest at 9.0 ms/row and
21.9 MB — roughly 26× faster and 7× smaller.

A second model (HistGB, for the average charging rate) was trained, shipped, measured
and then **removed**; Finding 1b below is why.

The headline figure is row-weighted blocked CV run *within each source*. See
[Protocol](#protocol) for why that qualifier matters.

---

## The data

Two CSVs, not one distribution:

| | `ev_charging_data1.csv` (`d1`) | `ev_charging_data2.csv` (`d2`) |
|---|---|---|
| rows | 3 006 | 9 086 |
| sessions | one continuous session | four sessions |
| cadence | 5 s | 10 s |
| power | 1–7 kW | 5–50 kW |
| voltage | 200–500 V | 300–800 V |
| SoC | 20–53 % | 20–24.2 % |
| month | **February only** | **March only** |
| day_of_week | Thursday only | Mon–Thu |
| hour | 3–7 | 0–6 |

They are a month apart and occupy different operating regimes. Any evaluation that mixes
them, and any feature that identifies them, will report a number that does not transfer.

---

## Finding 1 — the target is a cumulative ramp

`Energy Supplied_kWh` never decreases; it is a running counter of elapsed time.

```
d1:  corr(Energy, Duration) = +1.0000   corr(Energy, Power) = -0.0107   R²(E = mean_power × Duration) = 0.9999
d2:  corr(Energy, Duration) = +0.9997   corr(Energy, Power) = +0.0098   R²(E = mean_power × Duration) = 0.9994
```

Two consequences:

1. `corr(Energy, Power) ≈ 0` looks impossible for a real energy quantity
   (energy = power × time), so the column was first assumed to be synthesised as
   `k × duration`: `d1 k = 3.975 ± 0.101`, `d2 k = 27.495 ± 0.885`. **That
   inference was wrong** — see Finding 1b. The correlation is against the
   *instantaneous* power, and a running total legitimately decorrelates from it.
2. **A tree model cannot extrapolate a ramp.** It interpolates the durations it has
   seen perfectly and returns nonsense beyond them. Chronological holdout on `d1`:
   **R² = −3.16** (−3.26 under blocked CV).

`Time Elapsed_s` and `Charging_Duration_h` are therefore components of the target, not
causes of it. Inside `d1`, `State Of Charge_SoC` also correlates +1.0 with it — the
series was sampled while the battery charged, so SoC is a second elapsed-time clock.

### Three formulations, one protocol

Same data, same estimator, only the framing changes:

| formulation | blocked CV R² | chrono d1 | chrono d2 |
|---|---|---|---|
| predict kWh **with** duration | −0.0951 | **−3.16** | +0.996 |
| predict kWh **without** duration | −0.3302 | **−3.16** | +0.378 |
| **predict rate, then integrate** | **+0.9977** | +0.997 | +0.996 |

The adopted formulation predicts the average **charging rate** (kW), which is stationary
(CV 2.6 % / 3.2 %) and therefore generalises, and the view re-integrates it:
`energy = rate × duration`.

### Finding 1b — the second model was measured, and removed

The formulation above (`predict rate, integrate`) was shipped. It was **wrong**, and
the honest limitation that used to sit here has been replaced by what was actually
done about it.

**Three identities hold in the shipped CSVs.** Each removes a quantity a model could
have claimed to learn. None is a modelling claim — it is arithmetic on the raw
columns, and all three are reproduced by the notebook:

| identity | deviation |
|---|---|
| `Charging Power_kW = V × I / 1000` | **0.055 %** (d1), **0.136 %** (d2) |
| `Charging Rate_kW × Charging_Duration_h = Energy Supplied_kWh` | **exactly 0.00000000 kWh** |
| `Energy Supplied_kWh = ∫P dt` (per row, session clock reset) | **0.005 kWh** (d1), **0.098 kWh** (d2) |

**The second is decisive.** The pipeline predicted `Charging Rate_kW` and multiplied
it back by `Charging_Duration_h` — but those two columns *are* the target, by
construction. **The duration cancels**, so the headline **+0.9977** measures exactly
one thing: *can you predict `Energy / Duration`?*

And that quantity is a near-constant while its only physical driver is not:

| | CV of `Energy/Duration` | CV of `Charging Power_kW` | `corr(rate, power)` |
|---|---|---|---|
| d1 | **2.55 %** | **43.88 %** | **+0.0353** |
| d2 | **3.22 %** | **47.41 %** | **+0.0364** |

The target varies ~17× less than the power that drives it. A 2.6 % signal cannot
carry a 44 % response, and the correlation confirms it does not.

**A constant beats it.** `constant_k_r2` = **0.9999 (d1) / 0.9994 (d2)** — above the
+0.9977 that was published. The model is worse than doing nothing.

**What the shipped model actually did.** Driving the real serving path (scaler
included), the rate took **exactly two values** — 4.392 kW or 21.668 kW, stepping at
~7 kW. It had learned *"which dataset is this"* via a power threshold, nothing else.
On a 9-point V/I sweep, against the physical reference `P × duration`:

```
     V    I     P kW  served kWh  P x t kWh   error %
   250    5    1.250       8.783      2.500     251.3
   300   10    3.000       8.783      6.000      46.4
   400   15    6.000       8.783     12.000     -26.8
   450   20    9.000      43.335     18.000     140.8
   500   30   15.000      43.335     30.000      44.5
   600   40   24.000      43.335     48.000      -9.7
   650   55   35.750      43.345     71.500     -39.4
   700   65   45.500      43.345     91.000     -52.4
   780   80   62.400      43.345    124.800     -65.3
```

**75.2 % mean absolute error, 251.3 % worst case.** `400 V × 15 A × 2 h` is 12 kWh
physically; it answered 8.783 — identical to `250 V × 5 A`.

**Why retraining could not fix it.** The target a second model needs is
`power × duration`, and `power` is already pinned by `V × I` to within 0.14 %.
Both quantities are known the moment the operator presses submit — **nothing
remains to be learned.**

**Candidate formulations, measured** — same estimator (`energy_estimator`), same
protocol, same sweep, all regenerated by the notebook. Two columns, because they
answer two different questions:

| | formulation | blocked CV R² | mean served error |
|---|---|---|---|
| C1 | `[P, T, SoC] → Energy/Duration` *(shipped)* | +0.9977 | **74.7 %** |
| C2 | `[P, T, SoC] → P` | −8.3961 | **2.8 %** |
| C3 | `[V, I, T, SoC] → P` | −8.3927 | **2.8 %** |
| C4 | `[P, T, SoC, duration] → Energy` | −0.0951 | **82.3 %** |
| C5 | `[T, SoC] → Energy/(P × duration)` | −25.2308 | 36.0 % |
| **C6** | **no model: `energy = power × duration`** | exact | **0 %** |

The two rankings are almost reversed, and that is the point: **the published metric
was not merely insensitive — it was the only metric the shipped formulation could
pass**, because its output is algebraically the target. C2/C3 post a deeply negative
R² for the opposite reason — they emit a session *total*, which cannot match a
row-wise cumulative — while serving within **2.8 %**. C3 also duplicates the power
model, leaving the kWh figure out of step with the power figure beside it.
**Read the right-hand column for deployment: C6 wins on every axis.**

```python
charging_rate = max(charging_power, 0.0)          # sustained rate == forecast power
predicted_energy_kwh = charging_rate * duration   # arithmetic
```

Side benefits: one artifact load and one predict per request instead of two, and the
kWh figure can no longer disagree with the power figure beside it.

**What would be needed to learn energy instead:** a dataset whose sessions actually
vary in power — this one has five sessions, two regimes, and a stationary power series
in each.

---

## Finding 2 — a random split leaks

The series is sampled every 5–10 s, so row *t+1* is a near-duplicate of row *t*.
`train_test_split(random_state=42)` puts the duplicate in training while scoring *t*,
which inflates the shipped model's R² from 0.95 to 1.00.

The fix is a **chronological 80/20 holdout**: train on the past, test on the future,
within one source.

---

## Finding 3 — pooled CV collapses to 0.71

```
pooled TimeSeriesSplit across both sources : R² = +0.7086
per-source blocked CV (row-weighted)       : R² = +0.9960
```

One fold straddles the `d1 → d2` boundary: it trains on 1–7 kW / 200–500 V data and is
tested on 5–50 kW / 300–800 V data. Reporting a single pooled figure would understate a
model that scores 0.996 *inside* each regime. Cross-validation therefore runs
independently within each source, and the headline is a row-weighted mean of the two
(per-source rows are published alongside so the number is auditable).

---

## Finding 4 — calendar features are dataset identifiers

`month` alone names the source with 100 % accuracy (`d1` = February only, `d2` = March
only). Fed to a model alongside the physical inputs it becomes "which file is this".
Measured on the energy formulation that was later retired (Finding 1b):

| held fixed | `month` | no calendar | with calendar |
|---|---|---|---|
| 400 V / 40 A, 25 °C, SoC 50, 2 h | 2 | 43.34 kWh | **8.02 kWh** |
| same inputs | 3 | 43.34 kWh | **54.99 kWh** |

A **6.9× swing from the date alone**, at identical physical inputs. Meanwhile the
calendar model is flat in current (54.99 → 55.01 kWh from 10 A to 80 A).

Cost of dropping calendar features: **0.0016** of blocked-CV R² (0.9993 → 0.9977).
`is_weekend` is constant 0 and carries no information either way.

**Calendar features are kept for the `power` model**, where they are genuinely
informative: without `month` it misses V×I by 7.2 kW at 600 V / 80 A instead of 0.3 kW,
because `month` is what disambiguates the low- and high-power sources at a shared
(V, I) point.

### Cheapest leak detector

Fit one feature alone, in-sample:

```
d1  target = Energy Supplied_kWh
    State Of Charge_SoC      1.0000  <-- LEAK
    Time Elapsed_s           1.0000  <-- LEAK
    Charging_Duration_h      1.0000  <-- LEAK
    hour                     0.9538  <-- LEAK
    Charging Rate_kW         0.5347
    Charging Voltage_V       0.0677
```

A single column reaching ~1.0 is carrying the answer.

`State Of Charge_SoC` was nonetheless **retained** in that model: the correlation above
holds against the *cumulative* target, not against the rate it predicted. Dropping it
cost 0.0102 of blocked-CV R² (0.9977 → 0.9875). That was measured (`soc_ablation` in
`ml/artifacts/leakage_audit.json`), not assumed. The point survives the model's removal:
**a feature that leaks against one target may be legitimate against another, so ablate
rather than assume.**

---

## Model choice — XGBoost over RandomForest

On the `power` task, both reach the same place; the serving budget does not:

| | blocked CV R² | chrono d1 | chrono d2 | ms/row | MB |
|---|---|---|---|---|---|
| RandomForest | 0.9986 | 0.9990 | 0.9997 | ~30 | 109.78 |
| HistGradientBoosting | 0.9983 | 0.9983 | 0.9993 | 1.3 | 0.37 |
| **XGBoost (shipped)** | 0.9960 | 0.9960 | 0.9984 | **0.35** | **3.20** |

RandomForest buys 0.0026 of R² for a ~90× latency penalty and a 34× size penalty on a
model that serves one row per form submission. The serving budget is what fails.

---

## Artifacts

```
ml/artifacts/
    current.json                 {"power": "power-v1"}
    model.joblib, scaler.joblib   legacy alias == the active power model
    power-v1/   {model,scaler}.joblib + meta.json
    leakage_audit.json           the audit behind every claim above
```

`ml/artifacts` and the two training CSVs in `data/raw/` are **committed**, so a
fresh clone serves predictions straight after `migrate` and can retrain too.
Only *future* retrain output is ignored (`ml/artifacts/*-v[0-9]*`, except the
`power-v1` that `current.json` points at), so a retrain does not
silently add ~3.5 MB to the repo. Retrain with:

```bash
python scripts/train_models.py            # audit + model + terminal report
python scripts/train_models.py --report   # print the audit only
python scripts/train_models.py --only power
```

`_write_current` **prunes any pointer whose version directory is gone**, so retiring
a model cannot leave `current.json` naming a directory git never shipped. Training also
prints a shipping note if a freshly created version is not git-tracked.

Every `meta.json` is self-describing: feature order, per-feature input ranges, both
evaluation protocols, latency, size, training timestamp, library versions, the protocol
string and `derived` (the arithmetic that turns this model's output into kWh).
`core.model_registry` asserts the feature order at load time.

---

## Serving contract

```
form (V, I, temp, SoC, duration, timestamp)
    -> power  (XGBoost)      -> charging power kW
    -> rate   = power                  (sustained rate == forecast power)
    -> energy_kwh = power * duration   (arithmetic, not inference)
    -> I_fault = power * 1000 / V      (operator's V, not a 400 V assumption)
```

* Feature columns are built from `meta.json["features"]`, never hard-coded. A missing
  input raises rather than defaulting to 0.
* Input outside the recorded training range logs one `INFO` event per submit —
  `Input outside training distribution: hour=12 outside trained range [0, 7]; ...` —
  and the prediction proceeds, flagged as an extrapolation.
* Missing or unreadable artifacts degrade to a message plus a `PREDICTION_ERROR`
  event, never a 500 or a startup traceback.

Verified by `tests/test_ml_pipeline.py` against the shipped artifact:
feature order, both protocols, accuracy and size/latency budgets, the feature-frame
guard, the OOD guard, the serving chain (including that energy scales monotonically
with the operator's current), and the per-index prediction dict.

---

## Reproducing the analysis

`ml/notebooks/ev_charging_prediction.ipynb` walks the whole argument on top of
`ml/pipeline/` — the same package the Django view serves from, so the notebook and
production cannot drift:

1. Leakage audit (ramp diagnosis → three formulations → calendar ablation → univariate R²)
2. Feature contracts
3. Honest evaluation (chronological + blocked CV, per source, and the pooled-CV demonstration)
4. Model bake-off (accuracy, latency, size)
5. Train and export
6. Serving contract check against `meta.json`

Executes end-to-end in ~160 s.
