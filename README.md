<div align="center">

![header](https://capsule-render.vercel.app/api?type=waving&color=00C896&height=200&section=header&text=EVOptima&fontSize=55&fontColor=ffffff&fontAlignY=38&desc=Intelligent+EV+Charging+Monitoring+and+Prediction+System&descSize=15&descAlignY=58&descColor=c8fff2)

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-5.x-092E20?style=for-the-badge&logo=django&logoColor=white)
![ML](https://img.shields.io/badge/ML-XGBoost-FF6B35?style=for-the-badge&logo=scikitlearn&logoColor=white)
![WebSockets](https://img.shields.io/badge/WebSockets-Django_Channels-00C896?style=for-the-badge&logo=socket.io&logoColor=white)
![Tests](https://img.shields.io/badge/Tests-387_passing-22c55e?style=for-the-badge&logo=pytest&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge)

<br/>

> A **real-time EV charging intelligence platform** that predicts charging power and
> energy, monitors live charging parameters, detects faults instantly, and visualizes
> system performance — powered by **Django Channels WebSockets** and a leak-audited
> **XGBoost** pipeline (blocked-CV **R² = 0.996**).

<br/>

[![Patent](https://img.shields.io/badge/Patent-No.%20202521090973A-FFD700?style=flat-square)](https://github.com/hariom710)
[![GitHub](https://img.shields.io/badge/GitHub-hariom710-181717?style=flat-square&logo=github)](https://github.com/hariom710)
[![Portfolio](https://img.shields.io/badge/Portfolio-hariombalang.netlify.app-00C896?style=flat-square&logo=netlify)](https://hariombalang.netlify.app)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-hariombalang-0A66C2?style=flat-square&logo=linkedin)](https://linkedin.com/in/hariombalang)

</div>

---

## ✨ Key Features

<table>
<tr>
<td>

### ⚡ Energy Prediction
- **XGBoost predicts charging power** from V, I, temperature, SoC and calendar features
- Energy is **arithmetic, not inference**: `energy = power × duration` — and `power = V × I / 1000` to within 0.14 %, so both inputs are known at submit time. (`Energy` is exactly `rate × duration` and `∫P dt` to within 0.098 kWh per row.)
- Blocked-CV **R² = 0.996**, measured chronologically and published per source
- Full provenance shown in the UI: version, algorithm, target, R², latency
- **Every prediction explains itself**: exact TreeSHAP over the seven inputs, printed as `bias + contributions = answer` with the residual it leaves

</td>
<td>

### 📡 Real-Time Monitoring
- **WebSocket** streams via Django Channels at `/ws/monitoring/`
- Live Current, Voltage, Temperature dashboards
- Sub-second update latency per charging port

</td>
</tr>
<tr>
<td>

### 🚨 Fault Detection
- Threshold-based alerts for all parameters
- Instant notification on anomaly detection
- Event log with timestamps and severity levels

</td>
<td>

### 📊 Live Visualization
- **Chart.js** real-time line and bar charts
- Multi-port power allocation overview
- Historical session analytics dashboard

</td>
</tr>
<tr>
<td>

### 🔌 Smart Power Allocation
- Manages multiple EV charging ports simultaneously
- Load balancing logic prevents grid overload
- Per-session SoC and duration tracking

</td>
<td>

### 🔐 Secure Authentication
- Django session-based login system
- Role-protected views and API endpoints
- CSRF-hardened forms throughout

</td>
</tr>
<tr>
<td colspan="2">

### 📅 Smart Charging Scheduler
- **Linear program (`scipy.optimize.linprog`)** over hourly slots — no new dependency, no new data
- Minimises the time-of-use bill subject to **site capacity, per-port limits, arrival/departure windows and deadlines**
- **Never misses a deadline silently**: an infeasible request names the binding constraint — the session's own window, or the site capacity — and reports the shortfall in kWh
- **Greedy vs optimised side by side** at `/scheduling/`, both run over the same fleet: the greedy rule (the one the site runs today) misses 54 kWh on the demo fleet and still pays 3 % more
- Optional **peak weight λ** prices site peak in kW, turning the LP into demand-charge shaping

</td>
</tr>
</table>

---

## 🛠️ Technology Stack

| Layer | Technologies |
|---|---|
| **Frontend** | HTML5, CSS3, JavaScript, Bootstrap 5, Chart.js |
| **Backend** | Python 3.10+ (verified on 3.13), Django 5.2, Django REST Framework |
| **Real-Time** | Django Channels 4.x, WebSockets, Daphne (ASGI) |
| **Machine Learning** | XGBoost, scikit-learn, Pandas, NumPy, Joblib |
| **Database** | SQLite (dev) · PostgreSQL-ready (prod) |
| **Quality** | pytest + pytest-django, coverage, ruff |

---

## 🤖 Machine Learning Model

### How it works

One model, then arithmetic. The operator's electrical inputs become charging power;
energy, average rate and fault current all follow from it:

```
  Input                                  Model                       Output
┌─────────────────────┐            ┌──────────────────┐      ┌─────────────────────────┐
│ Charging Voltage    │ ─────────► │ power           │      │ charging power (kW)     │
│ Charging Current    │            │ XGBoost         │ ───► │                         │
│ Battery Temperature │            │ R² = 0.996      │      └───────────┬─────────────┘
│ State of Charge     │            └──────────────────┘                  │
│ hour / dow / month  │                                                  ▼
└─────────────────────┘                          energy  = power × duration
                                                  rate    = power
                                                  I_fault = power × 1000 / V
                                                            (operator's V, not 400 V)
```

### Why there is no second model

A HistGB model for the average charging rate **was** shipped first, and then removed,
because it cannot be learned from this data. Three identities hold in the shipped
CSVs, and each one removes a quantity a model could have claimed to learn:

| identity | deviation |
|---|---|
| `Charging Power_kW = V × I / 1000` | **0.055 %** (d1), **0.136 %** (d2) |
| `Charging Rate_kW × Charging_Duration_h = Energy Supplied_kWh` | **exactly 0.00000000 kWh** |
| `Energy Supplied_kWh = ∫P dt` (per row, session clock reset) | **0.005 kWh** (d1), **0.098 kWh** (d2) |

The second is decisive: the pipeline predicted `Charging Rate_kW` and multiplied it
back by `Charging_Duration_h`, but those two columns *are* the target — **the duration
cancels**. The published **+0.9977** therefore only ever asked *"can you predict
`Energy / Duration`?"*, and that quantity is a near-constant while its driver is not:

- **CV 2.55 % (d1) / 3.22 % (d2)** for `Energy / Duration`, against **43.88 % / 47.41 %**
  for `Charging Power_kW`, with `corr(rate, power) = +0.035`. A 2.6 % signal cannot
  carry a 44 % response.
- A model fitted on it learns a *threshold*, not a response: it answered **4.392 kW or
  21.668 kW and nothing in between**, and served **75.2 % mean absolute error** against
  `power × duration` (251.3 % worst case) — *worse than a constant*, which
  `constant_k_r2` scores at 0.9999 / 0.9994.
- Meanwhile `power × duration` needs no model at all: power is pinned by `V × I` to
  within 0.14 %, so both inputs are known the moment the operator presses submit.

Dropping it also halved inference: one artifact load and one predict per request, and
the kWh figure can no longer disagree with the power figure beside it.

### Performance

| Model | Algorithm | Target | Blocked CV R² | Chronological 80/20 | Latency | Size |
|---|---|---|---|---|---|---|
| `power` | `XGBRegressor` | `Charging Power_kW` | **+0.9960** | +0.9960 / +0.9984 | 0.34 ms/row | 3.20 MB |

**0.34 ms/row and 3.20 MB** — roughly **26× faster and 7× smaller** than the
RandomForest artifact it replaced (9.0 ms/row, 21.9 MB), at equivalent accuracy.

| | Value |
|---|---|
| **Validation** | Chronological 80/20 holdout **+** `TimeSeriesSplit(5)` blocked CV, run within each source |
| **Serialization** | `ml/artifacts/power-v1/{model,scaler}.joblib` + `meta.json` |
| **Feature order** | Recorded in `meta.json`, asserted by the loader at load time |
| **Provenance** | Version, algorithm, target, both R² figures, latency, size and training time shown on `/prediction/` |
| **Explanation** | Exact TreeSHAP per input beside each prediction: the model's bias, every contribution in kW, and the residual those contributions leave (≤ 5.1e-05 kW measured) |

> **Why not one number?** The two supplied datasets are different operating regimes a
> month apart. Pooled cross-validation scores **0.71** because one fold straddles that
> boundary — a model that scores 0.996 inside each regime. Both figures above are
> row-weighted over per-source results, published alongside so they are auditable.

Full diagnosis — cumulative-target ramp, split leakage, calendar-feature dataset
identification, and the model bake-off — in **[docs/ml-findings.md](docs/ml-findings.md)**.
The argument is reproduced end-to-end in
[`ml/notebooks/ev_charging_prediction.ipynb`](ml/notebooks/ev_charging_prediction.ipynb)
(~160 s), which imports the same `ml/pipeline/` package the Django view serves from.

```bash
python scripts/train_models.py            # leakage audit + model + report
python scripts/train_models.py --report   # print the audit only
python scripts/train_models.py --only power
```

---

## ⚠️ Fault Detection Thresholds

| Parameter | Min | Max | Alert Type |
|---|---|---|---|
| **Current** | 10 A | 30 A | 🔴 Critical |
| **Voltage** | 400 V | 460 V | 🟠 Warning |
| **Temperature** | 0 °C | 80 °C | 🔴 Critical |

Thresholds are stored as a single editable `Thresholds` row (admin / `/api/thresholds/`).
All breaches are logged with a timestamp, port ID and measured value. The fault check
uses `I = P × 1000 / V` with **the operator's stated voltage**, not a nominal 400 V.

---

## 🏗️ System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  BROWSER CLIENT                                                  │
│  Chart.js dashboards ◄─── WebSocket (/ws/monitoring/) ───►       │
│  Bootstrap 5 UI                Django Channels                   │
└────────────────────────┬─────────────────────────────────────────┘
                         │ HTTP / WS (ASGI, Daphne)
┌────────────────────────▼─────────────────────────────────────────┐
│  DJANGO APPLICATION LAYER                                        │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐  │
│  │ accounts   │  │ monitoring │  │ prediction │  │visualization│ │
│  │ auth views │  │ dashboards │  │ prediction │  │ analytics  │  │
│  │ registers  │  │ consumers  │  │ inference  │  │ charts     │  │
│  │            │  │ services/  │  │ + OOD guard│  │            │  │
│  │            │  │ thresholds │  │ provenance │  │            │  │
│  └────────────┘  └────────────┘  └─────┬──────┘  └────────────┘  │
│                                        │ core.model_registry     │
│                                        │ ml.pipeline (contracts) │
└────────────────────────┬───────────────┴─────────────────────────┘
                         │ Django ORM                 ▲
┌────────────────────────▼─────────────────────────────┴───────────┐
│  DATA LAYER                                    SQLite/PostgreSQL  │
│  Thresholds · Reading · EventLog · SimulationControl · User       │
│                                                                  │
│  ML ARTIFACTS (committed)       ml/artifacts/                    │
│  power-v1 · current.json · meta.json · audit JSON                     │
└──────────────────────────────────────────────────────────────────┘
```

---

## 📂 Project Structure

```
EVOptima/
├── manage.py
├── pyproject.toml               ← ruff + pytest + coverage config
├── db.sqlite3                   ← gitignored
│
├── config/                      ← Django project package
│   ├── settings/                ← base · dev · prod · test
│   ├── asgi.py                  ← ASGI entry point (WebSockets)
│   ├── urls.py
│   └── wsgi.py
│
├── apps/
│   ├── accounts/                ← login, register, logout
│   ├── monitoring/              ← dashboards, WebSocket consumer, services/
│   │   ├── consumers.py         ← /ws/monitoring/
│   │   ├── routing.py
│   │   └── services/            ← thresholds, faults, simulation, sources
│   ├── prediction/              ← inference view, OOD guard, provenance
│   │   ├── forms.py             ← V, I, temp, SoC, duration, timestamp
│   │   └── views.py
│   ├── scheduling/              ← /scheduling/ page + demo fleet (no models)
│   │   ├── fleet.py
│   │   └── views.py
│   └── visualization/           ← analytics charts
│
├── core/
│   ├── model_registry.py        ← lazy, thread-safe artifact loader
│   └── site.py                  ← TOTAL_POWER_KW, the one bus budget
│
├── optimization/                ← Smart Charging Scheduler (Django-free)
│   ├── session.py               ← one vehicle's request, in hours
│   ├── tariff.py                ← published time-of-use table
│   ├── solver.py                ← the LP + named-binding report
│   └── baseline.py              ← the site's current greedy rule
│
├── ml/
│   ├── pipeline/                ← config (feature contracts), data, evaluate,
│   │                              audit, train, explain — shared by training & serving
│   ├── artifacts/               ← trained model (committed, ~6.4 MB)
│   └── notebooks/
│       └── ev_charging_prediction.ipynb
│
├── scripts/
│   ├── train_models.py          ← retrain + export the model
│   ├── seed_db.py               ← demo readings/events
│   ├── run_sim (manage command) ← simulator
│   └── gate_*.py                ← smoke gates
│
├── requirements/
│   ├── base.txt · dev.txt · prod.txt
│
├── templates/ · static/         ← project-level templates and assets
├── tests/                       ← 387 tests
├── docs/
│   ├── ml-findings.md           ← the ML diagnosis
│   └── migration-notes.md
└── data/raw/                    ← training CSVs (committed, 1.2 MB)
```

---

## 🚀 Quick Start

### Prerequisites

| Tool | Version |
|---|---|
| Python | 3.10+ (verified on 3.13) |
| pip | Latest |

### Installation

**1. Clone the repository**
```bash
git clone https://github.com/hariom710/evoptima.git
cd evoptima
```

**2. Create and activate a virtual environment**
```bash
# Linux / macOS
python -m venv venv
source venv/bin/activate

# Windows
python -m venv venv
.\venv\Scripts\activate
```

**3. Install dependencies**
```bash
pip install -r requirements/dev.txt      # includes base.txt + pytest, ruff, notebook extras
# production: pip install -r requirements/prod.txt
```

**4. Apply migrations** — this also creates the default `Admin` account
```bash
python manage.py migrate
```

**5. The model comes pre-trained** — `ml/artifacts/` ships `power-v1` (XGBoost) already
trained, and `data/raw/` ships the two training CSVs, so `/prediction/` works
immediately after step 4. Retraining is optional:
```bash
python scripts/train_models.py    # re-runs the leakage audit and rewrites the model
```

**6. Optional: seed demo readings and events**
```bash
python scripts/seed_db.py
```

**7. Start the development server**
```bash
python manage.py runserver
```

Because Daphne is installed, `runserver` speaks ASGI and serves `/ws/monitoring/` too.

**8. Open in browser**
```
http://127.0.0.1:8000/
```

| | |
|---|---|
| Username | `Admin` |
| Password | `Admin@123` |

---

## ✅ Quality Gates

```bash
python -m ruff check .        # lint
python -m pytest              # 387 tests
python -m pytest --cov        # with coverage
python manage.py check        # Django system checks
```

Smoke gates for a running instance:

```bash
python scripts/gate_routes.py
python scripts/gate_ws.py ws://127.0.0.1:8000/ws/monitoring/
python scripts/gate_phase3.py
```

---

## 🔬 Routes

### Pages

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Root redirect → `/home/` |
| `GET` | `/home/` | Status homepage + recent events |
| `GET` | `/prediction/` | Three-port prediction form, per-input explanation and model provenance |
| `GET` | `/prediction/welcome/` | Prediction intro |
| `GET` | `/scheduling/` | Greedy vs optimised charge plan · `?capacity=` · `?peak_weight=` |
| `GET` | `/visualization/` | Analytics charts |
| `GET` | `/monitoring/dashboard/` | Live WebSocket dashboard |
| `GET` | `/accounts/login/` · `/register/` · `/logout/` | Authentication |
| `GET` | `/admin/` | Django admin |

### API

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/status/` | Latest reading and SAFE/FAULT state |
| `GET` | `/api/thresholds/` | Current threshold configuration |
| `GET` | `/api/events/` | Event log |
| `GET` | `/api/dashboard/` | Live fault dashboard (HTML, WebSocket-driven) |
| `GET` | `/api/monitoring/status/` · `/thresholds/` · `/events/` | Namespaced aliases |
| `POST` | `/api/monitoring/simulate/start/` · `/stop/` | Simulator control |
| `WS` | `ws://host/ws/monitoring/` | Live sensor stream |

`/api/monitoring/*` and `/api/*` expose the same views; the namespaces exist so the
`monitoring` app keeps a single URL namespace without a Django `urls.W005` clash.

---

## 📋 Module Summary

| Module | Responsibility | Pattern |
|---|---|---|
| **accounts** | Login, registration, logout | Django auth |
| **monitoring** | Dashboards, WebSocket consumer, thresholds, fault rules, simulator | Django Channels + services |
| **prediction** | Inference, OOD guard, per-input explanation, provenance card, DC-bus allocation | Registry + `ml.pipeline` |
| **scheduling** | `/scheduling/` comparison page and demo fleet | Thin view over `optimization` |
| **visualization** | Historical analytics and Chart.js charts | Django + DRF |
| **core** | Lazy, thread-safe artifact loading with remembered failures; shared site constants | Singleton + lock |
| **ml/pipeline** | Feature contracts, training, honest evaluation, leakage audit, TreeSHAP attribution | Shared by notebook and view |
| **optimization** | Charge scheduling: LP solver, tariff, greedy baseline | Pure functions, no Django |

---

## 🤝 Contributing

```bash
# 1. Fork the repo and create your feature branch
git checkout -b feature/your-feature

# 2. Commit your changes
git commit -m "feat: add your feature"

# 3. Push and open a Pull Request
git push origin feature/your-feature
```

Please run `ruff check .` and `pytest` before opening a PR.

---

## 📜 License

This project is licensed under the **MIT License**.

---

## 👤 Author

**Hariom Ashok Balang**

Trainee Analyst @ Capgemini · BTech Computer Technology, YCCE Nagpur (2022–2026)

| Platform | Link |
|---|---|
| 🌐 Portfolio | [hariombalang.netlify.app](https://hariombalang.netlify.app) |
| 💼 LinkedIn | [linkedin.com/in/hariombalang](https://linkedin.com/in/hariombalang) |
| 🐙 GitHub | [github.com/hariom710](https://github.com/hariom710) |
| 📧 Email | hariombalang@gmail.com |

---

![footer](https://capsule-render.vercel.app/api?type=waving&color=00C896&height=100&section=footer)
