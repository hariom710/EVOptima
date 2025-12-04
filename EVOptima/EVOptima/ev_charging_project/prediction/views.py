from django.shortcuts import render
from .forms import PredictionForm
import joblib
import pandas as pd
from datetime import datetime
import os
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from monitoring.services import check_fault, log_reading, log_event, PredictedValues

# Load the model and scaler
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
model = None
scaler = None

try:
    model_path = os.path.join(BASE_DIR, 'model', 'model.joblib')
    scaler_path = os.path.join(BASE_DIR, 'model', 'scaler.joblib')
    if os.path.exists(model_path) and os.path.exists(scaler_path):
        model = joblib.load(model_path)
        scaler = joblib.load(scaler_path)
    else:
        print(f"Warning: Model files not found at {model_path} or {scaler_path}")
except Exception as e:
    print(f"Error loading model files: {e}")

@login_required
def predict_view(request):
    if model is None or scaler is None:
        messages.error(request, 'Prediction model is not available. Please ensure model files are present.')
        forms = []
        for i in range(3):
            forms.append(PredictionForm(prefix=f'form{i}'))
        return render(request, 'prediction/predict.html', {
            'forms': forms,
            'predictions': [],
            'total_power': 100,
            'remaining_power': 100
        })
    
    predictions = []
    forms = []
    total_power = 100  # Main DC power in kW
    remaining_power = total_power

    if request.method == 'POST':
        valid_forms = []
        for i in range(3):  # Handle 3 forms
            form = PredictionForm(request.POST, prefix=f'form{i}')
            forms.append(form)
            if form.is_valid():
                valid_forms.append(form)

        # Process valid forms
        for form in valid_forms:
            # Derive charging power: prefer V*I/1000 if voltage/current provided
            input_power_kw = form.cleaned_data['charging_power']
            voltage = form.cleaned_data.get('voltage')
            current = form.cleaned_data.get('current')
            derived_power_kw = None
            if voltage is not None and current is not None and voltage > 0 and current > 0:
                derived_power_kw = (voltage * current) / 1000.0
            effective_power_kw = derived_power_kw if derived_power_kw is not None else input_power_kw

            charging_power = min(effective_power_kw, remaining_power)
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
            try:
                features_scaled = scaler.transform(features)
                prediction = model.predict(features_scaled)[0]
                
                # Convert prediction to expected values (assuming prediction is time in hours)
                # We'll derive current and voltage from the input parameters
                predicted_current = current if current else (charging_power * 1000) / (voltage if voltage else 230)
                predicted_voltage = voltage if voltage else 230
                predicted_temp = battery_temp
                
                # Create predicted values for fault detection
                predicted_values = PredictedValues(
                    current=predicted_current,
                    voltage=predicted_voltage,
                    temperature=predicted_temp
                )
                
                # Check for faults
                is_fault, fault_message = check_fault(predicted_values)
                
                # Log the reading
                log_reading(predicted_values)
                
                # Log fault if detected
                if is_fault:
                    log_event('PREDICTION_ERROR', f'Fault detected in prediction: {fault_message}', 
                             f'Predicted values: Current={predicted_current:.2f}A, Voltage={predicted_voltage:.2f}V, Temp={predicted_temp:.2f}°C')
                    messages.warning(request, f'Fault detected: {fault_message}')
                
                predictions.append({
                    'charging_power': charging_power,
                    'battery_temp': battery_temp,
                    'soc': soc,
                    'duration': duration,
                    'timestamp': timestamp,
                    'predicted_value': prediction,
                    'power_allocation': charging_power,
                    'fault_detected': is_fault,
                    'fault_message': fault_message if is_fault else None,
                    'predicted_current': predicted_current,
                    'predicted_voltage': predicted_voltage,
                })
                
                remaining_power -= charging_power
            except Exception as e:
                messages.error(request, f'Error making prediction: {str(e)}')
                log_event('PREDICTION_ERROR', f'Error in prediction: {str(e)}', 'Prediction failed')
                continue

    else:
        # Create 3 empty forms
        for i in range(3):
            forms.append(PredictionForm(prefix=f'form{i}'))

    # Create a dictionary mapping form index to predictions for easier template access
    predictions_dict = {}
    for idx, pred in enumerate(predictions):
        predictions_dict[idx] = pred
    
    return render(request, 'prediction/predict.html', {
        'forms': forms,
        'predictions': predictions,
        'predictions_dict': predictions_dict,
        'total_power': total_power,
        'remaining_power': remaining_power if predictions else total_power
    })


@login_required
def dashboard_view(request):
    """Unified dashboard view combining prediction and fault detection"""
    from monitoring.models import Thresholds, Reading, EventLog
    from django.forms.models import model_to_dict
    
    predictions = []
    forms = []
    
    # Handle POST requests for predictions
    if request.method == 'POST':
        valid_forms = []
        for i in range(3):
            form = PredictionForm(request.POST, prefix=f'form{i}')
            forms.append(form)
            if form.is_valid():
                valid_forms.append((i, form))
        
        # Process valid forms (same logic as predict_view)
        for idx, form in valid_forms:
            if model is None or scaler is None:
                continue
                
            input_power_kw = form.cleaned_data['charging_power']
            voltage = form.cleaned_data.get('voltage')
            current = form.cleaned_data.get('current')
            derived_power_kw = None
            if voltage is not None and current is not None and voltage > 0 and current > 0:
                derived_power_kw = (voltage * current) / 1000.0
            effective_power_kw = derived_power_kw if derived_power_kw is not None else input_power_kw
            
            battery_temp = form.cleaned_data['battery_temp']
            soc = form.cleaned_data['soc']
            duration = form.cleaned_data['duration']
            timestamp = form.cleaned_data['timestamp']
            
            features = pd.DataFrame({
                'Charging Power_kW': [effective_power_kw],
                'Battery Temperature_C': [battery_temp],
                'State Of Charge_SoC': [soc],
                'Charging_Duration_h': [duration],
                'hour': [timestamp.hour],
                'day_of_week': [timestamp.weekday()],
                'month': [timestamp.month],
                'is_weekend': [1 if timestamp.weekday() in [5, 6] else 0]
            })
            
            try:
                features_scaled = scaler.transform(features)
                prediction = model.predict(features_scaled)[0]
                
                predicted_current = current if current else (effective_power_kw * 1000) / (voltage if voltage else 230)
                predicted_voltage = voltage if voltage else 230
                
                predicted_values = PredictedValues(
                    current=predicted_current,
                    voltage=predicted_voltage,
                    temperature=battery_temp
                )
                
                is_fault, fault_message = check_fault(predicted_values)
                log_reading(predicted_values)
                
                if is_fault:
                    log_event('PREDICTION_ERROR', f'Fault detected in prediction: {fault_message}', 
                             f'Predicted values: Current={predicted_current:.2f}A, Voltage={predicted_voltage:.2f}V, Temp={battery_temp:.2f}°C')
                    messages.warning(request, f'Fault detected: {fault_message}')
                
                predictions.append({
                    'index': idx,
                    'charging_power': effective_power_kw,
                    'battery_temp': battery_temp,
                    'soc': soc,
                    'duration': duration,
                    'timestamp': timestamp,
                    'predicted_value': prediction,
                    'fault_detected': is_fault,
                    'fault_message': fault_message if is_fault else None,
                    'predicted_current': predicted_current,
                    'predicted_voltage': predicted_voltage,
                })
            except Exception as e:
                messages.error(request, f'Error making prediction: {str(e)}')
                log_event('PREDICTION_ERROR', f'Error in prediction: {str(e)}', 'Prediction failed')
    
    # Create empty forms if needed
    if not forms:
        for i in range(3):
            forms.append(PredictionForm(prefix=f'form{i}'))
    
    # Get latest readings and thresholds
    latest_reading = Reading.objects.order_by('-created_at').first()
    thresholds = Thresholds.objects.order_by('-updated_at').first()
    if not thresholds:
        thresholds = Thresholds.objects.create()
    
    # Get recent events
    recent_events = EventLog.objects.order_by('-created_at')[:20]
    
    # Determine current state
    state = 'SAFE'
    if latest_reading:
        if (latest_reading.current > thresholds.max_current or latest_reading.current < thresholds.min_current or
            latest_reading.voltage > thresholds.max_voltage or latest_reading.voltage < thresholds.min_voltage or
            latest_reading.temperature > thresholds.max_temperature or latest_reading.temperature < thresholds.min_temperature):
            state = 'FAULT'
    
    return render(request, 'prediction/dashboard.html', {
        'latest_reading': latest_reading,
        'thresholds': thresholds,
        'recent_events': recent_events,
        'state': state,
        'forms': forms,
        'predictions': predictions,
    })


@login_required
def welcome_view(request):
    """Welcome page for the system"""
    return render(request, 'prediction/welcome.html')