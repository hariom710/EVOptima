from django.shortcuts import render
from .forms import PredictionForm
import joblib
import pandas as pd
from datetime import datetime
import os
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from monitoring.services import check_fault, log_reading, log_event, PredictedValues
from django.core.mail import send_mail
from django.conf import settings

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
    total_power = 100  # Main DC power in kW (for allocation visualization)
    remaining_power = total_power
    DEFAULT_POWER_KW = 50.0
    ASSUMED_VOLTAGE_V = 400.0

    if request.method == 'POST':
        valid_forms = []
        for i in range(3):  # Handle 3 forms
            form = PredictionForm(request.POST, prefix=f'form{i}')
            forms.append(form)
            if form.is_valid():
                valid_forms.append((i, form))

        # Process valid forms
        for idx, form in valid_forms:
            # Use default nominal power for feature (no user input now)
            effective_power_kw = DEFAULT_POWER_KW
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
                # Check if both SOC and battery temperature are zero
                if soc == 0 and battery_temp == 0:
                    predicted_energy_kwh = 0
                    avg_power_kw = 0
                    predicted_voltage = 0
                    predicted_current = 0
                    predicted_temp = 0
                    is_fault = False
                    fault_message = None
                else:
                    features_scaled = scaler.transform(features)
                    prediction = model.predict(features_scaled)[0]
                    
                    # Derive power/current from model prediction (assumed energy in kWh)
                    predicted_energy_kwh = prediction
                    avg_power_kw = predicted_energy_kwh / duration if duration and duration > 0 else DEFAULT_POWER_KW
                    predicted_voltage = ASSUMED_VOLTAGE_V
                    predicted_current = (avg_power_kw * 1000.0) / predicted_voltage
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
                    # Attempt to email alert
                    try:
                        recipient = 'adityabhone032gmail.com'
                        # auto-fix missing '@' if looks like gmail
                        if recipient.endswith('gmail.com') and '@' not in recipient:
                            local = recipient.replace('gmail.com', '')
                            recipient = f"{local}@gmail.com"
                        send_mail(
                            subject='EV Charging Prediction Fault Alert',
                            message=f'Port {idx+1}: {fault_message}\nCurrent={predicted_current:.2f}A Voltage={predicted_voltage:.2f}V Temp={predicted_temp:.2f}°C',
                            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@example.com'),
                            recipient_list=[recipient],
                            fail_silently=True,
                        )
                    except Exception as e:
                        log_event('EMAIL_ERROR', f'Failed to send fault email: {str(e)}', 'Email alert failure')
                
                # Actual power to allocate should reflect predicted average demand but never exceed remaining
                used_power_kw = min(max(avg_power_kw, 0.0), remaining_power)

                predictions.append({
                    'index': idx,
                    'charging_power': charging_power,
                    'battery_temp': battery_temp,
                    'soc': soc,
                    'duration': duration,
                    'timestamp': timestamp,
                    'predicted_value': predicted_energy_kwh,
                    'power_allocation': used_power_kw,
                    'fault_detected': is_fault,
                    'fault_message': fault_message if is_fault else None,
                    'predicted_current': predicted_current,
                    'predicted_voltage': predicted_voltage,
                })
                
                # Decrease remaining power by actual used power (not the cap)
                remaining_power = max(0.0, remaining_power - used_power_kw)
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
    """Deprecated: kept for backward compatibility if routed; redirect to home."""
    from django.shortcuts import redirect
    return redirect('/')


@login_required
def home_view(request):
    """Minimal professional homepage with status and recent events."""
    from monitoring.models import Thresholds, Reading, EventLog
    latest_reading = Reading.objects.order_by('-created_at').first()
    thresholds = Thresholds.objects.order_by('-updated_at').first()
    if not thresholds:
        thresholds = Thresholds.objects.create()
    
    recent_events = EventLog.objects.order_by('-created_at')[:20]
    
    state = 'SAFE'
    if latest_reading:
        if (latest_reading.current > thresholds.max_current or latest_reading.current < thresholds.min_current or
            latest_reading.voltage > thresholds.max_voltage or latest_reading.voltage < thresholds.min_voltage or
            latest_reading.temperature > thresholds.max_temperature or latest_reading.temperature < thresholds.min_temperature):
            state = 'FAULT'
    
    return render(request, 'prediction/home.html', {
        'latest_reading': latest_reading,
        'state': state,
        'recent_events': recent_events,
    })


@login_required
def welcome_view(request):
    """Welcome page for the system"""
    return render(request, 'prediction/welcome.html')