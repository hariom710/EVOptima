# ⚡Advanced-Monitoring-and-Control-Mechanism-for-Electric-Vehicle-Charging-Using-Machine-Learning 🚗🔋

This project implements a machine learning model to predict EV charging demand (energy consumption in kWh) based on historical charging data.


## Project Structure
- `ev_charging_prediction.ipynb`: Main Jupyter notebook containing the ML pipeline
- `requirements.txt`: List of Python dependencies
- `ev_charging_data2.xlsx`: Input dataset (not included)
- `model.joblib`: Trained model (generated after running notebook)
- `scaler.joblib`: Feature scaler (generated after running notebook)

## Setup and Installation

1. Create a virtual environment (recommended):
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Place your `ev_charging_data2.xlsx` file in the project directory

4. Run the Jupyter notebook:
```bash
jupyter notebook ev_charging_prediction.ipynb
```

## Model Features
The model uses the following features to predict charging demand:
- Session duration (hours)
- Temperature (°C)
- Hour of day
- Day of week
- Month
- Weekend indicator

## Making Predictions
After training, you can use the `predict_charging_demand()` function to make predictions for new charging sessions. The function takes the following parameters:
- session_duration (float): Duration of charging session in hours
- temperature (float): Temperature in Celsius
- timestamp (datetime): Time of the charging session




![Python](https://img.shields.io/badge/Python-3.10-blue?style=flat&logo=python)
![Django](https://img.shields.io/badge/Django-4.0-green?style=flat&logo=django)
![React.js](https://img.shields.io/badge/React.js-18.0-blue?style=flat&logo=react)
![MySQL](https://img.shields.io/badge/MySQL-8.0-blue?style=flat&logo=mysql)
![License](https://img.shields.io/badge/License-MIT-green?style=flat)
![Contributions Welcome](https://img.shields.io/badge/Contributions-Welcome-brightgreen)

---

**Smart EV Charging Station - Monitoring & Control** is a **real-time monitoring and optimization system** designed for **intelligent EV charging management**.  
It leverages **IoT, Machine Learning, and Cloud Computing** to ensure:

✅ **Optimized Charging**: Adjust voltage/current for faster & safer charging.  
✅ **Energy Demand Prediction**: Use **Machine Learning** to forecast consumption.  
✅ **Fault Detection & Alerts**: Identify **overvoltage, overheating, and anomalies**.  
✅ **Grid Integration**: Load balancing for **efficient power distribution**.  
✅ **Real-time Monitoring**: Visualize data using **interactive dashboards**.  

---

## 🚀 Features

- 🔍 **Real-time Data Monitoring**: Voltage, Current, Power, Battery Temperature.
- ⚡ **Dynamic Charging Adjustment**: Auto-optimizes voltage/current to prevent overload.
- 📊 **ML-Based Demand Prediction**: Uses **Linear Regression, XGBoost, LSTMs** for forecasts.
- 🛠️ **Fault Detection & Alerts**: Detects **anomalies, power surges, and overheating**.
- 📡 **IoT Communication**: Uses **ESP32/Raspberry Pi** with **MQTT for live data**.
- 📊 **Web Dashboard**: **React.js frontend** for real-time data visualization.

---

## 🛠️ Technologies Used

| **Technology** | **Purpose** |
|---------------|------------|
| **Django** | Backend & API Handling |
| **Django REST Framework** | API Development |
| **React.js** | Interactive Web Dashboard |
| **Scikit-Learn (ML)** | Energy Consumption Prediction |
| **PostgreSQL/MySQL** | Charging Session Data Storage |
| **MQTT (IoT Protocol)** | Real-time Sensor Data Transmission |
| **ESP32/Raspberry Pi** | Sensor Data Collection |
| **Docker** | Containerized Deployment |
| **AWS / Heroku** | Cloud Hosting |

---

## 📋 Prerequisites
Before running this project, make sure you have:

- [Python 3.10+](https://www.python.org/downloads/)
- [Node.js 16+](https://nodejs.org/)
- [MySQL Server 8+](https://dev.mysql.com/downloads/)
- [ESP32 Firmware Flasher](https://espressif.github.io/esptool/)
- [Docker](https://www.docker.com/)

---


## 📂 Directory Structure  

> ```
> EV_Charging_Project/
> │── backend/                 # Django Backend
> │   ├── manage.py
> │   ├── settings.py
> │   ├── models.py            # Database models
> │   ├── views.py             # API endpoints
> │   ├── ml_model/            # Machine Learning Model
> │   │   ├── train.py         # Model training script
> │   │   ├── predict.py       # Predictive analysis
> │── frontend/                # React Dashboard
> │   ├── src/
> │   │   ├── components/
> │   │   ├── pages/
> │   │   ├── App.js           # Main React file
> │── microcontroller/         # ESP32 Firmware
> │   ├── main.py              # Reads sensor & sends data via MQTT
> │── deployment/              # Deployment Scripts
> │   ├── docker-compose.yml
> │   ├── nginx.conf
> │── README.md
> ```


Customization (Optional): Feel free to modify the content, add more destinations, or change the visuals to fit your own safari packages.

# 🤝 Contributingg
We welcome contributions to Banking Application If you'd like to add new features follow these steps:

Fork the repository.
Create a new branch (git checkout -b feature-branch).
Add your changes (git add .).
Commit your changes (git commit -m 'Add new feature').
Push to the branch (git push origin feature-branch).
Open a Pull Request to the main branch.
Before submitting a pull request, ensure your code is properly formatted and includes necessary comments.

# 🧑‍💻 Code of Conduct
This project follows the Contributor Covenant Code of Conduct. We are committed to maintaining a harassment-free environment for all participants.

Please follow the guidelines outlined in the CODE_OF_CONDUCT.md file for more details.

# 📜 License
This project is licensed under the MIT License - see the LICENSE.md file for details.

### Key Sections:

- **Features**: Highlighted key features of the Banking Application.
- **Technologies Used**: Outlined the core technologies used for building the website (Java).
- **How to Use**: Provided clear steps on how to clone and run the project.
- **Contributing**: Explained how to contribute to the repository.
- **License**: Basic information about the license for the project.

🌟 Acknowledgements
Built with ❤️ using Java and MySQL.
Special thanks to open-source contributors and the development community.

This `README.md` provides an organized and clear introduction to your **Banking Application** project. You can expand or adjust the sections as your project grows.
