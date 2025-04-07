# EV Charging Demand Prediction

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
