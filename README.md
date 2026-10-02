<div align="center">

![header](https://capsule-render.vercel.app/api?type=waving&color=00C896&height=200&section=header&text=EVOptima&fontSize=55&fontColor=ffffff&fontAlignY=38&desc=Intelligent+EV+Charging+Monitoring+and+Prediction+System&descSize=15&descAlignY=58&descColor=c8fff2)

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-5.x-092E20?style=for-the-badge&logo=django&logoColor=white)
![ML](https://img.shields.io/badge/ML-XGBoost_+_HistGB-FF6B35?style=for-the-badge&logo=scikitlearn&logoColor=white)
![WebSockets](https://img.shields.io/badge/WebSockets-Django_Channels-00C896?style=for-the-badge&logo=socket.io&logoColor=white)
![Tests](https://img.shields.io/badge/Tests-289_passing-22c55e?style=for-the-badge&logo=pytest&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge)

<br/>

> A **real-time EV charging intelligence platform** that predicts charging power and
> energy, monitors live charging parameters, detects faults instantly, and visualizes
> system performance — powered by **Django Channels WebSockets** and a two-stage
> **XGBoost + HistGradientBoosting** pipeline (blocked-CV **R² = 0.996 / 0.998**).

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
- **Two-model chain**: XGBoost predicts charging power, HistGB predicts the average charging rate
- Energy derived as `rate × duration` — never predicted as a cumulative ramp
- Blocked-CV **R² = 0.996** (power) / **0.998** (energy), measured chronologically
- Full provenance shown in the UI: version, algorithm, target, R², latency

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
</table>

---

## 🛠️ Technology Stack

| Layer | Technologies |
|---|---|
| **Frontend** | HTML5, CSS3, JavaScript, Bootstrap 5, Chart.js |
| **Backend** | Python 3.10+ (verified on 3.13), Django 5.2, Django REST Framework |
| **Real-Time** | Django Channels 4.x, WebSockets, Daphne (ASGI) |
| **Machine Learning** | XGBoost, HistGradientBoosting, scikit-learn, Pandas, NumPy, Joblib |
| **Database** | SQLite (dev) · PostgreSQL-ready (prod) |
| **Quality** | pytest + pytest-django, coverage, ruff |

---

## 🤖 Machine Learning Model

### How it works

Two models are chained. The first turns the operator's electrical inputs into charging
power; the second turns that power plus battery state into an average charging *rate*,
which the view integrates over the requested duration:

```
  Input                                  Model                       Output
┌─────────────────────┐            ┌──────────────────┐      ┌─────────────────────────┐
│ Charging Voltage    │ ─────────► │ power           │      │ charging power (kW)     │
│ Charging Current    │            │ XGBoost         │ ───► │                         │
│ Battery Temperature │            │ R² = 0.996      │      └───────────┬─────────────┘
│ State of Charge     │            └──────────────────┘                  │
│ hour / dow / month  │                                                  ▼
└─────────────────────┘            ┌──────────────────┐      ┌─────────────────────────┐
                                   │ energy          │      │ rate (kW)               │
                    power ───────► │ HistGB          │ ───► │                         │
                    temp  ───────► │ R² = 0.998      │      │ energy = rate × duration│
                    SoC   ───────► └──────────────────┘      └─────────────────────────┘
```

### Performance

| Model | Algorithm | Target | Blocked CV R² | Chronological 80/20 | Latency | Size |
|---|---|---|---|---|---|---|
| `power` | `XGBRegressor` | `Charging Power_kW` | **+0.9960** | +0.9960 / +0.9984 | 0.34 ms/row | 3.20 MB |
| `energy` | `HistGradientBoostingRegressor` | `Charging Rate_kW` | **+0.9977** | +0.9968 / +0.9955 | 0.83 ms/row | 0.27 MB |

Combined **1.21 ms/row and 3.47 MB** — roughly 7× faster and 6× smaller than the
RandomForest artifact it replaced (9.0 ms/row, 21.9 MB), at the same accuracy.

| | Value |
|---|---|
| **Validation** | Chronological 80/20 holdout **+** `TimeSeriesSplit(5)` blocked CV, run within each source |
| **Serialization** | `ml/artifacts/{power,energy}-v1/{model,scaler}.joblib` + `meta.json` |
| **Feature order** | Recorded in `meta.json`, asserted by the loader at load time |
| **Provenance** | Version, algorithm, target, both R² figures, latency, size and training time shown on `/prediction/` |

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
python scripts/train_models.py            # leakage audit + both models + report
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
│  │ auth views │  │ dashboards │  │ two-model  │  │ analytics  │  │
│  │ registers  │  │ consumers  │  │ chain +    │  │ charts     │  │
│  │            │  │ services/  │  │ provenance │  │            │  │
│  │            │  │ thresholds │  │            │  │            │  │
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
│  power-v1 · energy-v1 · current.json · meta.json · audit JSON    │
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
│   ├── prediction/              ← the two-model inference view
│   │   ├── forms.py             ← V, I, temp, SoC, duration, timestamp
│   │   └── views.py
│   └── visualization/           ← analytics charts
│
├── core/
│   └── model_registry.py        ← lazy, thread-safe artifact loader
│
├── ml/
│   ├── pipeline/                ← config (feature contracts), data, evaluate,
│   │                              audit, train — shared by training & serving
│   ├── artifacts/               ← trained models (committed, ~3.7 MB)
│   └── notebooks/
│       └── ev_charging_prediction.ipynb
│
├── scripts/
│   ├── train_models.py          ← retrain + export both models
│   ├── seed_db.py               ← demo readings/events
│   ├── run_sim (manage command) ← simulator
│   └── gate_*.py                ← smoke gates
│
├── requirements/
│   ├── base.txt · dev.txt · prod.txt
│
├── templates/ · static/         ← project-level templates and assets
├── tests/                       ← 289 tests
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

**5. Models come pre-trained** — `ml/artifacts/` ships `power-v1` (XGBoost) and
`energy-v1` (HistGB) already trained, and `data/raw/` ships the two training CSVs,
so `/prediction/` works immediately after step 4. Retraining is optional:
```bash
python scripts/train_models.py    # re-runs the leakage audit and rewrites both models
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
python -m pytest              # 289 tests
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
| `GET` | `/prediction/` | Three-port prediction form + model provenance |
| `GET` | `/prediction/welcome/` | Prediction intro |
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
| **prediction** | Two-model inference, OOD guard, provenance card, DC-bus allocation | Registry + `ml.pipeline` |
| **visualization** | Historical analytics and Chart.js charts | Django + DRF |
| **core** | Lazy, thread-safe artifact loading with remembered failures | Singleton + lock |
| **ml/pipeline** | Feature contracts, training, honest evaluation, leakage audit | Shared by notebook and view |

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
