# Migration Notes — EVOptima Repository Restructure

This file records what moved, what was merged, and every judgement call made
while collapsing the repository into a single production-grade Django project.

---

## Phase 0 — Repo hygiene

- Added `.gitignore` (pycache, `.env`, `*.joblib`, `db.sqlite3`, `staticfiles/`,
  `data/raw/`, notebook checkpoints, test/lint caches, editor folders).
- Untracked from git **but kept on disk**:
  - 3 x `db.sqlite3` (1.3 MB + 1.1 MB + 0.6 MB)
  - 2 x `staticfiles/` (collectstatic output, incl. admin vendor JS)
  - 4 x `model.joblib` (21.9 MB each) and 4 x `scaler.joblib`
  - duplicate CSV/XLSX datasets and one duplicate notebook copy
  - **All duplicates verified byte-identical by MD5 before untracking.**
- Committed the pending deletions of `Documents/` (108 files), `CT23/` (17 files)
  and `banner_final.gif`. They remain available in git history (history was
  deliberately **not** rewritten — repo stays at 119 MB).

---

## Phase 1 — Collapse to one project + merge fault2

### Duplicate Django projects: A vs B

Two near-identical copies existed:

| | Path | Files | Verdict |
|---|---|---|---|
| **A** | `EVOptima/ev_charging_project/` | 60 | **kept — newer** |
| **B** | `EVOptima/EVOptima/ev_charging_project/` | 59 | deleted |

A is ahead in every differing file (`monitoring/views.py` 25 unique lines vs 3,
`prediction/views.py` 62 vs 48). B was kept only pending a review of its
supposedly-unique content.

### Manual reconciliation of the three "B-unique" files

The original diff flagged three files containing lines absent from A. Each was
reviewed line-by-line rather than blind-copied:

**1. `prediction/forms.py` — 7 lines only in B → NOT ported.**
B declares `charging_power`, `voltage`, `current` fields; A does not. This is a
deliberate evolution, not a regression: A's `prediction/views.py` contains
`# Use default nominal power for feature (no user input now)` and reads exactly
`battery_temp`, `soc`, `duration`, `timestamp` — the four fields A declares.
A is self-consistent; B is the older 5-field version. Porting would have
reintroduced fields whose values no view reads.

**2. `prediction/templates/prediction/dashboard.html` — 3 lines only in B → NOT ported.**
The three lines render the same `charging_power`/`voltage`/`current` fields
removed above. Must stay in lockstep with `forms.py`.

**3. `visualization/templates/visualization/index.html` — 83 lines only in B → NOT ported.**
A is a strict superset in substance: 5 canvases (current, voltage, power,
temperature, **fault timeline**) vs B's 4, plus the active-fault banner,
safety-threshold cards and simulation controls. Every JS construct that
appeared "missing" from A (`createChart` x9, `maxTicksLimit`, `plugins`,
`legend`, `clearTimeout(updateTimeout)`) was grepped and confirmed present in
A. The only genuinely B-only construct was a dead `{% if error %}` alert block
— A's `visualization/views.py` never populates an `error` context key (it
renders without chart data and falls back to the live fault view instead).

### Layout changes

| Before | After |
|---|---|
| `EVOptima/ev_charging_project/manage.py` | `manage.py` (repo root) |
| `EVOptima/ev_charging_project/ev_charging_project/` | `config/` |
| `EVOptima/ev_charging_project/{accounts,monitoring,prediction,visualization}/` | `apps/{...}/` |
| `EVOptima/ev_charging_project/static/` | `static/` (root) |
| `EVOptima/ev_charging_project/model/{model,scaler}.joblib` | `ml/artifacts/` (committed) |
| `EVOptima/ev_charging_project/data/*.csv` | `data/raw/` (committed — the two training CSVs) |
| `EVOptima/ev_charging_prediction.ipynb` | `ml/notebooks/` |
| `EVOptima/*.csv`, `*.xlsx` | `data/raw/` |
| `staticfiles/`, `db.sqlite3`, duplicate `EVOptima/EVOptima/` | deleted / regenerated |
| `EVOptima/README.md` (stale — described a React/MySQL/MQTT/ESP32 stack the code does not use) | deleted; root `README.md` is the single source of truth |

### fault2 merge

`fault2/` was a second Django project (package `evfault`) providing the
real-time layer. It was folded into `apps/monitoring/` as **one** app so that
the three models (`Thresholds`, `Reading`, `EventLog`) exist exactly once:

| From `fault2/` | Into |
|---|---|
| `monitoring/consumers.py` | `apps/monitoring/consumers.py` |
| `monitoring/routing.py` | `apps/monitoring/routing.py` |
| `monitoring/services.py` (`CsvPredictionSource`, monitor loop) | `apps/monitoring/services/sources.py` |
| `monitoring/views.py` (DRF `status`/`thresholds`/`events`/`dashboard`) | `apps/monitoring/views.py` |
| `monitoring/management/commands/start_monitor.py` | `apps/monitoring/management/commands/` |
| `monitoring/management/commands/reset_thresholds.py` | `apps/monitoring/management/commands/` |
| `templates/dashboard.html` | `templates/monitoring/dashboard.html` |
| `evfault/asgi.py` (`ProtocolTypeRouter` + `AuthMiddlewareStack`) | `config/asgi.py` |
| `monitoring/migrations/0001-0003` | superseded — see below |

**Model state:** A's `monitoring/models.py` already matches the merged shape
(`min/max_current`, `min/max_voltage`, `min/max_temperature`), i.e. it equals
fault2's state after its `0003_thresholds_min_temperature_and_more` migration.
A single fresh `0001_initial` was generated for the merged app; fault2's three
migrations were not carried over.

**Dev database reset:** both `db.sqlite3` files were local development data
produced entirely by the simulation commands (`simulate_normal_charging`,
`simulate_fault_detection`, `start_monitor`). No migration path is provided —
run `python manage.py migrate` to rebuild and `python manage.py reset_thresholds`
to repopulate the safety thresholds.

**Endpoints:** fault2's DRF endpoints are registered at their original routes
(`/api/status/`, `/api/thresholds/`, `/api/events/`, `/monitoring/dashboard/`)
so the existing WebSocket dashboard keeps working unchanged. The canonical
namespace (`/api/monitoring/...`) and the fault2 aliases are separate url
modules so no URL namespace is registered twice (`urls.W005`).

**Route parity note:** fault2 mounted its dashboard at `/` and `/api/`. Bare
`/` is claimed by project A's `root_redirect`, so the dashboard is served at
`/monitoring/dashboard/` (canonical) plus `/api/dashboard/` for parity.

### Files reviewed and deliberately NOT carried over

- `fault2/data/ev_charging_data.csv` — MD5-different from every dataset in
  `data/raw/` but **numerically identical**: same header, 3007 rows, and
  columns 3/4/5 (voltage, current, temperature) match to 1e-9 on every row.
  The only differences are timestamp formatting (`20/02/2025 3:26` vs
  `20-02-2025 03:26`) and float padding (`20.000` vs `20`). Redundant copy,
  deleted rather than duplicated into `data/raw/`.
- `fault2/monitoring/tests.py`, `apps/*/tests.py` — empty stubs; the real test
  suite lives in `tests/` (Phase 4).
- `fault2/monitoring/admin.py` — byte-identical to the kept copy.
- `fault2/.vscode/`, all `__pycache__/*.pyc` — now gitignored, untracked here.

### Latent defect found while merging the CSV source

`ev_charging_data2.csv` carries an extra `Time-lap` column at index 1, so its
columns are shifted by one relative to `ev_charging_data1.csv`:

| file | voltage | current | temperature |
|---|---|---|---|
| `ev_charging_data1.csv` | 3 | 4 | 5 |
| `ev_charging_data2.csv` | **4** | **5** | **6** |
| `ev_charging_data2_reordered.csv` | 3 | 4 | 5 |

The old hard-coded indices `(3, 4, 5)` therefore read *power* as voltage,
*voltage* as current and *current* as temperature when replaying `data2` —
silently feeding wrong units into fault detection. `CsvPredictionSource` now
resolves columns **by header name** (`resolve_columns()`), falling back to the
legacy positions only when no header matches. Verified correct against all
four datasets.

---

## Phase 2 - Configuration & dependencies

### Settings split (`config/settings/{base,dev,prod}.py`)

| file | role |
|---|---|
| `base.py` | shared only: no `SECRET_KEY`, no `DEBUG`, no `ALLOWED_HOSTS`, no channel layer |
| `dev.py` | `DEBUG=True`, SQLite, `InMemoryChannelLayer`, console email, readable logs |
| `prod.py` | everything read from the environment; Redis channel layer, JSON logs |

`manage.py`, `config/asgi.py` and `config/wsgi.py` all default to
`config.settings.dev` through `DJANGO_SETTINGS_MODULE`.

`.env` is optional and loaded from the repo root via `python-dotenv`.
`load_dotenv()` never overwrites a variable already present in the process
environment, so Docker/CI exports always win over a shipped `.env` file —
verified by the four-case probe (`.env` applied, real env overrides `.env`,
absent `.env` → defaults). `.env` is gitignored; `.env.example` is explicitly
un-ignored and documents every variable each settings module reads.

`DATA_DIR` and `MODEL_DIR` are now env-overridable (defaults unchanged:
`data/raw/`, `ml/artifacts/`).

### Production hardening added in `prod.py`

- **whitenoise** inserted directly after `SecurityMiddleware`, serving hashed +
  pre-compressed static files from the ASGI process itself.
- `STATICFILES_STORAGE` (removed in Django 5) replaced with the modern
  `STORAGES["staticfiles"]` pointing at
  `whitenoise.storage.CompressedManifestStaticFilesStorage`.
- **Structured logging**: `core.logging.JsonFormatter` emits one JSON object per
  line (timestamp, level, logger, message, traceback, caller `extra`) — what log
  shippers expect. `dev.py` keeps the human-readable console format.
- Existing security settings retained: HSTS (1 year, include-subdomains, preload),
  SSL redirect, secure session/CSRF cookies, `SECURE_PROXY_SSL_HEADER`.

### Dependencies: one pin set

The three inconsistent requirement files (project A `>=4.2`, fault2 `==5.0.6`,
nested copy `>=4.2`) are replaced by:

| file | contents |
|---|---|
| `requirements/base.txt` | `Django==5.2.15`, channels, daphne, DRF, crispy-forms, sklearn/pandas/numpy/joblib, python-dotenv |
| `requirements/dev.txt` | `-r base.txt` + pytest, pytest-django, coverage, ruff, matplotlib/seaborn/openpyxl (notebook-only) |
| `requirements/prod.txt` | `-r base.txt` + gunicorn, whitenoise, redis, channels-redis, `psycopg[binary]` |

**`daphne` is in `base`, not `prod`** — it is referenced from `INSTALLED_APPS`,
so `manage.py runserver` only speaks ASGI (and serves `/ws/monitoring/`) when
daphne is installed. Dev needs it too.

All three files were verified with `pip install --dry-run` (resolution only)
and then installed for real: `dev.txt` pulled in pytest-django 4.14.0,
coverage 7.16.1, matplotlib 3.11.2; `prod.txt` pulled in psycopg 3.3.1.

### Gate results

| gate | result |
|---|---|
| `manage.py check` (dev) | no issues |
| `manage.py makemigrations --check` | no changes detected |
| **`manage.py check --deploy` (prod, env set)** | **no issues — 0 warnings, 0 errors** |
| `scripts/gate_settings.py` | 45/45 assertions |
| `.env` loading probe (4 cases) | 4/4 |
| Phase 1 HTTP gate re-run | 14/14 (no regression) |
| Phase 1 WebSocket + routes gates | pass |
| `git check-ignore .env` | ignored via `.gitignore:15` |

`scripts/gate_deploy.py` exports a full production environment (50+ char secret,
`DJANGO_DEBUG=0`, hosts, CSRF origins, `DATABASE_URL`, `REDIS_URL`) and fails
on *any* `--deploy` warning, so W004/W008/W009/W012/W016/W018/W020/W021/W022
cannot regress silently.

---

## Phase 3 - Code quality

### Lazy model registry (`core/model_registry.py`)

`apps/prediction/views.py` deserialised `model.joblib` + `scaler.joblib`
(~21 MB) **at module import**, so every management command, every test run and
every ASGI worker paid that cost before doing anything useful — and a missing
artifact raised while Django was still importing the URLconf, turning a
deployment problem into an opaque startup traceback.

`core.model_registry.registry` loads on first *use* instead:

- `threading.Lock` held for the whole load, so concurrent requests cannot race
  into loading the artifacts twice (gate proves all 8 concurrent callers get
  the *same* object);
- three states — `EMPTY` / `LOADED` / `FAILED` — and a **failed load is
  remembered**, so a broken artifact produces one cheap `ModelNotAvailable`
  rather than re-parsing 21 MB on every request;
- the view degrades to a user-facing message plus a `PREDICTION_ERROR` event.

`core/` is deliberately a plain package (not in `INSTALLED_APPS`) so settings,
commands, tests and training scripts can import it without the app registry.

### Latent defects found and fixed

| # | Defect | Impact | Fix |
|---|---|---|---|
| 1 | `predict_view` zero branch (`soc==0 and battery_temp==0`) never assigned `predicted_values`, yet `log_reading(predicted_values)` ran unconditionally | **`NameError` on every such submit**, swallowed by the blanket `except` into a misleading "Error making prediction" | leave it `None` and skip the log |
| 2 | `start_simulation` / `stop_simulation` returned a DRF `Response` from a *plain Django* view (`@require_http_methods`, no `@api_view`) | `.accepted_renderer` never set -> **every AJAX Start/Stop click returned HTTP 500** | `@api_view(["POST"])` |
| 3 | WebSocket consumer checked only `temperature > max_temperature` | low-temp fault reported **FAULT on the API but SAFE on the WS dashboard** | mirror `views.status` exactly |
| 4 | UTF-8 degree-Celsius symbol written as cp1252 mojibake in prediction fault messages | garbled email/event text | emit `\u00b0C` |
| 5 | `EMAIL_ERROR` missing from `EventLog.EVENT_TYPES` | value invisible in the admin dropdown, fails `full_clean()` | added to choices |
| 6 | 12 dead imports across `apps/` | noise, misleading readers | removed (ruff F401) |

Defect 1 also protects the status endpoints: persisting I=0/V=0/T=0 would be
read by `/status/` and `/home/` as an out-of-range **FAULT**.

### Database indexes (migration `0002`)

`Reading.created_at`, `EventLog.created_at` and `EventLog.event_type` are now
indexed. `/status/`, `/home/` and the WebSocket consumer all do
`order_by("-created_at").first()` and readings arrive ~1/sec during a
simulation, so without them every poll was a full scan.

### Simulation control plane — `_simulation_threads` removed

The module-level `views._simulation_threads: dict[str, threading.Thread]` had
three fatal properties: it only covered the worker that handled the click (a
second gunicorn worker saw an empty dict and its Stop button stopped
nothing), it was lost on restart, and it orphaned the loop.

Replaced with a database-backed control plane:

| piece | role |
|---|---|
| `SimulationControl` (pk=1) | `requested_type` written by the web tier, `active_type` + `heartbeat_at` written by the daemon |
| `manage.py run_sim --daemon` | polls the row every tick, starts/stops accordingly, heartbeats, **clears both on any exit** so a Ctrl+C can never leave the UI claiming a simulation is live |
| `manage.py run_sim --type normal\|fault` | one-shot; replaces the two near-duplicate commands `simulate_normal_charging` and `simulate_fault_detection`, which were **deleted** |
| `services.run_sample()` | one sample, split out of `run_simulation()` so the daemon can drive the loop a tick at a time |
| views `start/stop` | write intent only; `start` reports `simulator: online\|offline` and, when offline, tells the operator to run `run_sim --daemon` |
| `/status/` payload | now includes `simulation: {requested, active, simulator_alive, running, stale}` |
| visualization JS | shows the **server's** message instead of unconditionally claiming "started" |

One-shot mode deliberately does *not* write `SimulationControl`: it is a local
CLI run, and claiming the shared control plane would fight a daemon that is
following the web UI.

Cleanup therefore has two layers: a graceful exit (Ctrl+C, error) runs
`_release()` and clears `active_type`/`heartbeat_at`; a **hard kill** — which
never runs `finally` — is covered by `SimulationControl.HEARTBEAT_TIMEOUT`
(10 s), after which `daemon_alive` and `is_running` go false on their own.
Both paths were exercised against a live daemon.

`run_sim --daemon` does **not** call `close_old_connections()`. The loop issues
a query every tick so the connection is never idle, and with the default
`CONN_MAX_AGE=0` that call would expire and close the connection out from under
an open transaction.

### Threshold caching with `post_save` invalidation

`services.faults.get_thresholds()` caches the singleton row in-process — it was
being queried on every sample (~1 Hz during a simulation), on every `/status/`
poll (every 1.5 s) and on every prediction form.

- **Same process:** `post_save` / `post_delete` receivers are wired in
  `apps/monitoring/apps.py`, so an edit through the API or admin takes effect
  on the very next sample.
- **Other processes:** a signal only reaches the process that fired it, so a
  5 s TTL bounds staleness across workers independently of the signals.

The cache uses a **`threading.RLock`, not a `Lock`**: `get_thresholds()` holds
it while calling `Thresholds.objects.create()`, which fires `post_save` ->
`invalidate_thresholds()` on the *same thread*. A non-reentrant lock would
self-deadlock there — the gate exercises exactly this path with a 5 s timeout.

`monitor.py` deliberately keeps its `select_for_update()`: it holds a row lock
while evaluating and tripping the breaker, which is a correctness property, not
a hot-path inefficiency. That is the one intentional exception to the cache.

`reset_thresholds` writes through the signals (`delete` + `create`), so it needs
no explicit invalidation.

### `manage.py prune_readings`

Readings grow ~1/sec forever and nothing ever deleted them. Two independent,
individually disableable bounds:

- `--days N` (default 30) — age bound, a plain indexed `filter().delete()`
- `--keep N` (default 10000) — size bound; a sliced queryset cannot go
  straight to `delete()`, so the overflow primary keys are materialised first
- `--dry-run` reports without deleting

### Gate results (Phase 3)

| gate | result |
|---|---|
| **`scripts/gate_phase3.py`** | **77/77 assertions** |
| `manage.py check` | no issues |
| `manage.py makemigrations --check` | no changes detected |
| `python -m ruff check apps config core scripts` | all checks passed |
| `scripts/audit_encoding.py` | 82 tracked text files clean UTF-8 |
| dev DB after the gate | unchanged (single transaction, rolled back) |

The gate covers: import laziness, cache-hit/miss query counts, 8-thread
single-load, cached failure, the RLock auto-create deadlock path, index
introspection, all `SimulationControl` liveness states, `prune_readings` in all
four configurations, `run_sim` one-shot and a live daemon driven through
start -> stop -> restart by mutating the control row mid-run, the view
regressions, and the `NameError` fix.

---

## Defects fixed — cross-phase index

Full detail lives in the phase section that fixed them; this is the at-a-glance
list.

| # | Defect | Fixed in |
|---|---|---|
| 1 | Build artifacts and ~40 MB of non-source material tracked; no `.gitignore` | Phase 0 |
| 2 | Two complete Django projects (`EVOptima/` wrappers) coexisting with diverged code | Phase 1 |
| 3 | `CsvPredictionSource` ignored its `csv_path` argument and hardcoded `'data/ev_charging_data.csv'` | Phase 1 |
| 4 | Hard-coded column indices `(3, 4, 5)` read power/voltage/current as the wrong columns for `ev_charging_data2.csv` — silently feeding wrong units into fault detection | Phase 1 |
| 5 | Hardcoded `SECRET_KEY`, `DEBUG=True`, `ALLOWED_HOSTS=[]` | Phase 2 |
| 6 | Three inconsistent requirement files (Django `>=4.2` / `==5.0.6` / `>=4.2`) | Phase 2 |
| 7 | `prediction/views.py` loaded `model.joblib` + `scaler.joblib` (21 MB) at **module import** | Phase 3 — `core/model_registry.py` |
| 8 | `monitoring/views.py` kept simulation threads in a module-level `_simulation_threads` dict — lost on restart, invisible to other workers | Phase 3 — `run_sim --daemon` + `SimulationControl` |
| 9 | `predict_view` `NameError` when `soc == 0 and battery_temp == 0` | Phase 3 |
| 10 | AJAX simulation Start/Stop returned **HTTP 500** (bare DRF `Response` from a plain Django view) | Phase 3 |
| 11 | WebSocket consumer ignored `min_temperature`, disagreeing with the REST status | Phase 3 |
| 12 | cp1252 mojibake on the degree-Celsius symbol in fault messages | Phase 3 |
| 13 | `EMAIL_ERROR` logged but absent from `EventLog.EVENT_TYPES` | Phase 3 |
| 14 | No indexes on `Reading.created_at` / `EventLog.*` while readings arrive ~1/sec | Phase 3 |
| 15 | `Thresholds` re-queried on every sample and every status poll | Phase 3 |

---

## Remaining tech debt (deliberately out of scope)

- **SQLite → PostgreSQL**: dev uses SQLite; `prod.py` is Postgres-ready via
  `DATABASE_URL` but untested against a live Postgres instance.
- **Model retraining pipeline**: `ml/training/train.py` reproduces the artifact
  but is not wired to CI or a schedule; metrics live in `ml/artifacts/metrics.json`.
- **TimescaleDB / downsampling**: `Reading` is a high-write time series. Pruning
  keeps it bounded but a dedicated time-series store would be the next step.
- **Auth hardening**: default admin seeding and password policy still basic.
