from django.shortcuts import render
from .forms import PredictionForm
import joblib
import pandas as pd
from datetime import datetime
import os

# Load the model and scaler
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
model = joblib.load(os.path.join(BASE_DIR, 'model', 'model.joblib'))
scaler = joblib.load(os.path.join(BASE_DIR, 'model', 'scaler.joblib'))

def predict_view(request):
    prediction = None
    if request.method == 'POST':
        form = PredictionForm(request.POST)
        if form.is_valid():
            # Get form data
            charging_power = form.cleaned_data['charging_power']
            battery_temp = form.cleaned_data['battery_temp']
            soc = form.cleaned_data['soc']
            duration = form.cleaned_data['duration']
            timestamp = form.cleaned_data['timestamp']

            # Create features DataFrame
            features = pd.DataFrame({
                'Charging Power_kW': [charging_power],
                'Battery Temperature_C': [battery_temp],
                'State Of Charge_SoC': [soc],
                'Charging_Duration_h': [duration],
                'hour': [timestamp.hour],
                'day_of_week': [timestamp.weekday()],
                'month': [timestamp.month],
                'is_weekend': [1 if timestamp.weekday() in [5, 6] else 0]
            })

            # Scale features and make prediction
            features_scaled = scaler.transform(features)
            prediction = model.predict(features_scaled)[0]
    else:
        form = PredictionForm()

    return render(request, 'prediction/predict.html', {
        'form': form,
        'prediction': prediction
    })