<div align="center">

![header](https://capsule-render.vercel.app/api?type=waving&color=00C896&height=200&section=header&text=EVOptima&fontSize=55&fontColor=ffffff&fontAlignY=38&desc=Intelligent%20EV%20Charging%20Monitoring%20%26%20Prediction%20System&descSize=15&descAlignY=58&descColor=c8fff2)

![Python](https://img.shields.io/badge/Python-3.10-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-5.x-092E20?style=for-the-badge&logo=django&logoColor=white)
![RandomForest](https://img.shields.io/badge/ML-Random_Forest-FF6B35?style=for-the-badge&logo=scikitlearn&logoColor=white)
![WebSockets](https://img.shields.io/badge/WebSockets-Django_Channels-00C896?style=for-the-badge&logo=socket.io&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge)

<br/>

> A **real-time EV charging intelligence platform** that predicts energy consumption,  
> monitors live charging parameters, detects faults instantly, and visualizes system  
> performance — powered by **Django Channels WebSockets** and **Random Forest ML** (R² = 0.99).

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
- **Random Forest Regression** model (R² = 0.99)
- Predicts energy supplied, power draw, and estimated current
- Trained on real charging session data with Scikit-Learn

</td>
<td>

### 📡 Real-Time Monitoring
- **WebSocket** streams via Django Channels
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
| **Backend** | Python 3.10, Django 5.x, Django REST Framework |
| **Real-Time** | Django Channels, WebSockets (ASGI) |
| **Machine Learning** | Random Forest, Scikit-Learn, Pandas, NumPy, Joblib |
| **Database** | SQLite (dev) · PostgreSQL-ready (prod) |
| **Deployment** | Docker, Nginx, Gunicorn + Daphne (ASGI) |

---

## 🤖 Machine Learning Model

### How It Works

```
  Input Features                   Model                  Output
┌─────────────────┐           ┌───────────────┐      ┌──────────────────┐
│ Charging Power  │           │               │      │ Energy Supplied  │
│ Battery Temp    │  ──────►  │ Random Forest │  ──► │ Predicted Power  │
│ State of Charge │           │  Regression   │      │ Estimated Current│
│ Charging Hours  │           │  (R² = 0.99)  │      └──────────────────┘
│ Timestamp       │           └───────────────┘
└─────────────────┘
```

### Model Performance

| Metric | Value |
|---|---|
| **R² Score** | ~0.99 |
| **Algorithm** | Random Forest Regressor |
| **Library** | Scikit-Learn |
| **Serialization** | `model.joblib` + `scaler.joblib` |
| **Feature Engineering** | StandardScaler normalization |

---

## ⚠️ Fault Detection Thresholds

| Parameter | Min | Max | Alert Type |
|---|---|---|---|
| **Current** | 10 A | 30 A | 🔴 Critical |
| **Voltage** | 400 V | 460 V | 🟠 Warning |
| **Temperature** | 0 °C | 80 °C | 🔴 Critical |

All threshold breaches are logged with a timestamp, port ID, and measured value.

---

## 🏗️ System Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  BROWSER CLIENT                                              │
│  Chart.js dashboards ←──── WebSocket (ws://) ────►          │
│  Bootstrap 5 UI             Django Channels                  │
└────────────────────────┬─────────────────────────────────────┘
                         │ HTTP / WS (ASGI)
┌────────────────────────▼─────────────────────────────────────┐
│  DJANGO APPLICATION LAYER                                    │
│  ┌─────────────┐  ┌───────────────┐  ┌──────────────────┐   │
│  │  monitoring │  │  prediction   │  │  fault_detection │   │
│  │  views.py   │  │  views.py     │  │  services.py     │   │
│  │  consumers  │  │  ml_model/    │  │  thresholds      │   │
│  └─────────────┘  └───────────────┘  └──────────────────┘   │
└────────────────────────┬─────────────────────────────────────┘
                         │ Django ORM
┌────────────────────────▼─────────────────────────────────────┐
│  DATA LAYER          SQLite / PostgreSQL                     │
│  ChargingSession · SensorReading · FaultLog · EVPort         │
└──────────────────────────────────────────────────────────────┘
```

---

## 📂 Project Structure

```
EVOptima/
├── manage.py
├── requirements.txt
├── db.sqlite3
│
├── evoptima/                   ← Django project config
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py                 ← ASGI entry point (WebSockets)
│   └── wsgi.py
│
├── monitoring/                 ← Real-time monitoring app
│   ├── models.py               ← ChargingSession, SensorReading
│   ├── views.py
│   ├── consumers.py            ← WebSocket consumers
│   ├── services.py             ← Fault detection logic
│   └── templates/
│
├── prediction/                 ← ML prediction app
│   ├── models.py
│   ├── views.py
│   ├── ml_model/
│   │   ├── model.joblib        ← Trained Random Forest model
│   │   └── scaler.joblib       ← Feature scaler
│   └── templates/
│
└── static/
    ├── css/
    ├── js/                     ← Chart.js + WebSocket client
    └── img/
```

---

## 🚀 Quick Start

### Prerequisites

| Tool | Version |
|---|---|
| Python | 3.10+ |
| pip | Latest |
| Docker | Optional (production) |

### Installation

**1. Clone the repository**
```bash
git clone https://github.com/hariom710/evoptima.git
cd evoptima
```

**2. Create and activate virtual environment**
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
pip install -r requirements.txt
```

**4. Apply migrations**
```bash
python manage.py migrate
```

**5. Start the development server**
```bash
python manage.py runserver
```

**6. Open in browser**
```
http://127.0.0.1:8000/
```

---

### 🐳 Docker (Production)

```bash
# Build and start all services
docker-compose up --build

# WebSocket connections routed by Nginx → Daphne (ASGI)
# REST API served via Gunicorn
```

---

## 🔬 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/sessions/` | List all charging sessions |
| `POST` | `/api/predict/` | Run energy prediction |
| `GET` | `/api/faults/` | Fetch fault log |
| `WS` | `ws://host/ws/monitor/` | Live sensor data stream |

---

## 📋 Module Summary

| Module | Responsibility | Pattern |
|---|---|---|
| **monitoring** | WebSocket consumers, live sensor ingestion, dashboard views | Django Channels |
| **prediction** | ML inference endpoint, model loading, feature preprocessing | Random Forest |
| **fault_detection** | Threshold evaluation, alert generation, event logging | Rule-based |
| **static/js** | Chart.js rendering, WebSocket client, real-time UI updates | Vanilla JS |

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

---

## 📜 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

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
