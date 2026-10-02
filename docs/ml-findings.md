# ML findings — EV charging prediction

What was wrong with the shipped model, how it was diagnosed, and what replaced it.

**TL;DR:** the previously deployed model reported R² ≈ 1.00 from a random split over a
cumulative target. Under an honest time split it scored **−2.9**. The target is now the
*charging rate* rather than cumulative kWh, the split is chronological, calendar features
were removed from the energy model, and both models were retrained:

| model | algorithm | target | blocked CV R² | chrono 80/20 | latency | size |
|---|---|---|---|---|---|---|
| `power` | `XGBRegressor` | `Charging Power_kW` | **+0.9960** | +0.9960 / +0.9984 | 0.34 ms/row | 3.20 MB |
| `energy` | `HistGradientBoostingRegressor` | `Charging Rate_kW` | **+0.9977** | +0.9968 / +0.9955 | 0.83 ms/row | 0.27 MB |

Combined **1.21 ms/row and 3.47 MB**, against the previous RandomForest at 9.0 ms/row and
21.9 MB — roughly 7× faster and 6× smaller.

Both figures are row-weighted blocked CV run *within each source*. See
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

1. **`corr(Energy, Power) ≈ 0`** is impossible for a real energy quantity
   (energy = power × time). The column was synthesised as `k × duration`:
   `d1 k = 3.975 ± 0.101`, `d2 k = 27.495 ± 0.885`.
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

**Honest limitation.** Within a source, `Energy = k × Duration` and `corr(rate, power)
= +0.035`. The kWh column genuinely does not vary with power in this dataset, so *no*
model can make predicted energy respond continuously to current here. What is learnable
is which regime applies (power ≤ 4 kW → `d1`'s 3.97, power ≥ 7 kW → `d2`'s 27.50, with
the two overlapping only in 4–7 kW), and the model learns that. Predicting a continuous
power response would require a dataset whose energy column is actually power-integrated.

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
only). Fed to the energy model it becomes "which file is this":

| held fixed | `month` | deployed model | with calendar |
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

`State Of Charge_SoC` is nonetheless **retained** in the deployed energy model: the
correlation above holds against the *cumulative* target, not against the rate the
deployed model predicts. Dropping it costs 0.0102 of blocked-CV R² (0.9977 → 0.9875).
That was measured (`soc_ablation` in `ml/artifacts/leakage_audit.json`), not assumed.

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
    current.json                 {"power": "power-v1", "energy": "energy-v1"}
    model.joblib, scaler.joblib   legacy alias == the active energy model
    power-v1/   {model,scaler}.joblib + meta.json
    energy-v1/  {model,scaler}.joblib + meta.json
    leakage_audit.json           the audit behind every claim above
```

`ml/artifacts` is **gitignored** (model binaries are build output). Retrain with:

```bash
python scripts/train_models.py            # audit + both models + terminal report
python scripts/train_models.py --report   # print the audit only
python scripts/train_models.py --only power
```

Every `meta.json` is self-describing: feature order, per-feature input ranges, both
evaluation protocols, latency, size, training timestamp, library versions and the
protocol string. `core.model_registry` asserts the feature order at load time.

---

## Serving contract

```
form (V, I, temp, SoC, duration, timestamp)
    -> power  (XGBoost)      -> charging power kW
    -> energy (HistGB)       -> average charging rate kW
    -> energy_kwh = rate * duration
    -> I_fault = power * 1000 / V        (operator's V, not a 400 V assumption)
```

* Feature columns are built from `meta.json["features"]`, never hard-coded. A missing
  input raises rather than defaulting to 0.
* Input outside the recorded training range logs one `INFO` event per submit —
  `Input outside training distribution: hour=12 outside trained range [0, 7]; ...` —
  and the prediction proceeds, flagged as an extrapolation.
* Missing or unreadable artifacts degrade to a message plus a `PREDICTION_ERROR`
  event, never a 500 or a startup traceback.

Verified by `tests/test_ml_pipeline.py` (30 tests) against the shipped artifacts:
feature order, both protocols, accuracy and size/latency budgets, the feature-frame
guard, the OOD guard, the two-model chain, and the per-index prediction dict.

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
