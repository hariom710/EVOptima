"""Prediction views.

Serving is deliberately thin. Every feature name, its order and its training
range come from ``meta.json`` beside the artifacts, so the view cannot drift
from what the models were fitted on -- the old code hard-coded a nine-column
DataFrame whose order happened to match, and whose ``Charging Power_kW`` entry
was a constant 50.0 that the training data never contained (range 1.0-7.0 kW in
one source, 5.0-50.0 in the other, mean 3.96).

One model is used, then arithmetic::

    form (V, I, temp, SoC, duration, timestamp)
        -> power  (XGBoost)  -> charging power kW
        -> energy_kwh = charging_power_kw * duration

The rate in the UI is that same power: ``Charging Rate_kW x
Charging_Duration_h`` equals ``Energy Supplied_kWh`` exactly in both CSVs, and
``Charging Power_kW`` equals ``V x I / 1000`` to within 0.14 %, so the average
rate of a session is its sustained power. Folding the second model out means
one artifact load and one predict per request instead of two, and the energy
figure no longer disagrees with the power figure beside it (it used to report
8.78 kWh for both 250 V / 5 A and 400 V / 15 A over the same two hours).

Artifacts are not loaded at import time: importing this module used to
deserialise ~21 MB per worker before Django could serve a request, and a
missing file raised while Django was still importing the URLconf.
``core.model_registry`` loads them on first use instead, and failure degrades
to a user-facing message rather than a startup traceback.
"""
import pandas as pd
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.shortcuts import render

from apps.monitoring.models import EventLog, Reading
from apps.monitoring.services import (
    PredictedValues,
    check_fault,
    get_thresholds,
    log_event,
    log_reading,
)
from core.model_registry import ModelNotAvailable, registry
from core.site import TOTAL_POWER_KW
from ml.pipeline.config import POWER_MODEL_NAME
from ml.pipeline.data import calendar_values
from ml.pipeline.explain import ExplanationNotSupported, explain_power

from .forms import PredictionForm


def _feature_frame(inputs: dict, features: list) -> pd.DataFrame:
    """Build one row in exactly the order ``meta.json`` specifies.

    A missing input raises rather than silently defaulting to 0 -- feeding a
    model a column it was never trained on is the failure mode this pipeline
    exists to prevent.
    """
    missing = [name for name in features if name not in inputs]
    if missing:
        raise ModelNotAvailable(
            "no value for model input(s): "
            + ", ".join(missing)
            + " - meta.json and the serving code disagree"
        )
    return pd.DataFrame([[inputs[name] for name in features]], columns=features)


def _out_of_range(inputs: dict, ranges: dict) -> list:
    """Compare operator input against the min/max recorded at training time."""
    warnings = []
    for name, value in inputs.items():
        bounds = ranges.get(name)
        if not bounds:
            continue
        low, high = bounds
        if value < low or value > high:
            warnings.append(f"{name}={value:g} outside trained range [{low:g}, {high:g}]")
    return warnings


def _model_info(meta: dict) -> dict:
    """Fields the template shows, so a prediction is attributable to a version."""
    evaluation = meta.get("evaluation", {})
    return {
        "version": meta.get("version"),
        "algorithm": meta.get("algorithm"),
        "target": meta.get("target"),
        "derived": meta.get("derived"),
        "r2": evaluation.get("weighted_blocked_cv_r2"),
        "chronological_r2": evaluation.get("chronological_holdout"),
        "protocol": meta.get("protocol"),
        "latency_ms": meta.get("latency_ms_1row"),
        "size_mb": round((meta.get("size_bytes_model") or 0) / 1e6, 2),
        "trained_at": meta.get("trained_at"),
    }


def _context(forms, predictions, remaining_power, models=None):
    """Shared render payload -- including on the degraded path, so the template
    never has to test for a missing ``predictions_dict``."""
    return {
        "forms": forms,
        "predictions": predictions,
        "predictions_dict": {pred["index"]: pred for pred in predictions},
        "total_power": TOTAL_POWER_KW,
        "remaining_power": remaining_power,
        "models": models or {},
    }


@login_required
def predict_view(request):
    try:
        power_model, power_scaler = registry.get(POWER_MODEL_NAME)
        power_meta = registry.meta(POWER_MODEL_NAME)
    except ModelNotAvailable as exc:
        messages.error(
            request,
            "Prediction models are not available. Please ensure model files are present.",
        )
        log_event(
            "PREDICTION_ERROR", f"Model artifacts unavailable: {exc}", "Prediction unavailable"
        )
        forms = [PredictionForm(prefix=f"form{i}") for i in range(3)]
        return render(
            request, "prediction/predict.html", _context(forms, [], TOTAL_POWER_KW)
        )

    models = {
        POWER_MODEL_NAME: _model_info(power_meta),
    }

    predictions = []
    forms = []
    remaining_power = TOTAL_POWER_KW
    ood_notes = []
    unexplained = []

    if request.method == "POST":
        valid_forms = []
        for i in range(3):  # Handle 3 forms
            form = PredictionForm(request.POST, prefix=f"form{i}")
            forms.append(form)
            if form.is_valid():
                valid_forms.append((i, form))

        # Process valid forms
        for idx, form in valid_forms:
            battery_temp = form.cleaned_data["battery_temp"]
            soc = form.cleaned_data["soc"]
            duration = form.cleaned_data["duration"]
            timestamp = form.cleaned_data["timestamp"]
            voltage = form.cleaned_data["voltage"]
            current = form.cleaned_data["current"]

            try:
                # Per-port copy of the out-of-range check. `ood_notes` below is
                # the once-per-submit log entry; this one is what the card
                # shows, so a stray column is flagged beside the prediction it
                # actually affects.
                port_ood: list = []

                # Check if both SOC and battery temperature are zero: no vehicle
                # is connected, so there is nothing to predict and nothing to
                # measure. `predicted_values` stays None so the reading below is
                # skipped -- persisting I=0/V=0/T=0 would otherwise be read by
                # /status/ and /home/ as an out-of-range FAULT.
                if soc == 0 and battery_temp == 0:
                    predicted_values = None
                    predicted_energy_kwh = 0.0
                    charging_power = 0.0
                    charging_rate = 0.0
                    predicted_voltage = 0.0
                    predicted_current = 0.0
                    predicted_temp = 0.0
                    is_fault = False
                    fault_message = None
                    # Nothing to attribute: the 0 kW above is the view's own
                    # "no vehicle connected" decision, not a model output, and
                    # explaining it would invent a reasoning the model never
                    # produced.
                    explanation = None
                else:
                    inputs = {
                        "Charging Voltage_V": voltage,
                        "Charging Current_A": current,
                        "Battery Temperature_C": battery_temp,
                        "State Of Charge_SoC": soc,
                        "Charging_Duration_h": duration,
                        **calendar_values(timestamp),
                    }

                    port_ood = _out_of_range(
                        inputs, power_meta.get("input_ranges", {})
                    )
                    ood_notes.extend(port_ood)

                    # 1. power model: electrical inputs -> charging power (kW)
                    power_row = _feature_frame(inputs, power_meta["features"])
                    scaled_row = power_scaler.transform(power_row)
                    charging_power = float(power_model.predict(scaled_row)[0])

                    # 1b. attribute that same number across the inputs that
                    #     produced it. The transformed row is reused rather
                    #     than rebuilt, so the explanation is provably of the
                    #     prediction just made -- not of a second pass over a
                    #     row that happens to look similar.
                    try:
                        explanation = explain_power(
                            model=power_model,
                            scaled_row=scaled_row,
                            feature_names=power_meta["features"],
                            raw_inputs=inputs,
                            predicted_kw=charging_power,
                        )
                    except ExplanationNotSupported as exc:
                        # Not a prediction failure and not a silent gap: the
                        # reason the block is missing is recorded once per
                        # submit (three ports would otherwise say it three
                        # times) and the prediction itself stands.
                        unexplained.append(str(exc))
                        explanation = None

                    # 2. the average charging rate *is* the sustained power.
                    #    rate x duration equals Energy exactly, and P equals
                    #    V*I/1000 to within 0.14 %, so once P is known the rate
                    #    is known -- a second model would only re-derive it (see
                    #    ml/pipeline/__init__.py for the measurements).
                    charging_rate = max(charging_power, 0.0)

                    # 3. integrate the rate over the requested duration
                    predicted_energy_kwh = (
                        charging_rate * duration if duration > 0 else 0.0
                    )

                    # I = P / V. Voltage is the operator's stated value rather
                    # than a nominal 400 V, so the fault check sees the actual
                    # operating point instead of an assumption.
                    predicted_voltage = voltage
                    predicted_current = (charging_power * 1000.0) / voltage
                    predicted_temp = battery_temp

                    predicted_values = PredictedValues(
                        current=predicted_current,
                        voltage=predicted_voltage,
                        temperature=predicted_temp,
                    )
                    is_fault, fault_message = check_fault(predicted_values)

                # Log the reading
                if predicted_values is not None:
                    log_reading(predicted_values)

                # Log fault if detected
                if is_fault:
                    log_event(
                        "PREDICTION_ERROR",
                        f"Fault detected in prediction: {fault_message}",
                        f"Predicted values: Current={predicted_current:.2f}A, "
                        f"Voltage={predicted_voltage:.2f}V, "
                        f"Temp={predicted_temp:.2f}°C",
                    )
                    messages.warning(request, f"Fault detected: {fault_message}")
                    # Attempt to email alert
                    try:
                        recipient = "adityabhone032gmail.com"
                        # auto-fix missing '@' if looks like gmail
                        if recipient.endswith("gmail.com") and "@" not in recipient:
                            local = recipient.replace("gmail.com", "")
                            recipient = f"{local}@gmail.com"
                        send_mail(
                            subject="EV Charging Prediction Fault Alert",
                            message=f"Port {idx + 1}: {fault_message}\n"
                            f"Current={predicted_current:.2f}A "
                            f"Voltage={predicted_voltage:.2f}V "
                            f"Temp={predicted_temp:.2f}°C",
                            from_email=getattr(
                                settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"
                            ),
                            recipient_list=[recipient],
                            fail_silently=True,
                        )
                    except Exception as e:
                        log_event(
                            "EMAIL_ERROR",
                            f"Failed to send fault email: {str(e)}",
                            "Email alert failure",
                        )

                # Allocate against the average charging rate, capped by what is
                # left on the main DC bus.
                used_power_kw = min(charging_rate, remaining_power)

                predictions.append({
                    "index": idx,
                    "charging_power": charging_power,
                    "charging_rate": charging_rate,
                    "explanation": explanation,
                    "ood_notes": port_ood,
                    "battery_temp": battery_temp,
                    "soc": soc,
                    "duration": duration,
                    "timestamp": timestamp,
                    "predicted_value": predicted_energy_kwh,
                    "power_allocation": used_power_kw,
                    "fault_detected": is_fault,
                    "fault_message": fault_message if is_fault else None,
                    "predicted_current": predicted_current,
                    "predicted_voltage": predicted_voltage,
                })

                # Decrease remaining power by actual used power (not the cap)
                remaining_power = max(0.0, remaining_power - used_power_kw)
            except Exception as e:
                messages.error(request, f"Error making prediction: {str(e)}")
                log_event("PREDICTION_ERROR", f"Error in prediction: {str(e)}", "Prediction failed")
                continue

    else:
        # Create 3 empty forms
        for i in range(3):
            forms.append(PredictionForm(prefix=f"form{i}"))

    # One event per submit rather than per port: the same input repeats across
    # ports, and three identical warnings say nothing extra.
    if ood_notes:
        unique = list(dict.fromkeys(ood_notes))
        log_event(
            "INFO",
            "Input outside training distribution: " + "; ".join(unique),
            "Prediction proceeded; treat the result as an extrapolation",
        )

    if unexplained:
        log_event(
            "INFO",
            "No per-feature attribution: " + "; ".join(dict.fromkeys(unexplained)),
            "Breakdown omitted; the prediction itself is unaffected",
        )

    return render(
        request,
        "prediction/predict.html",
        _context(
            forms,
            predictions,
            remaining_power if predictions else TOTAL_POWER_KW,
            models=models,
        ),
    )


@login_required
def dashboard_view(request):
    """Deprecated: kept for backward compatibility if routed; redirect to home."""
    from django.shortcuts import redirect
    return redirect('/')


@login_required
def home_view(request):
    """Minimal professional homepage with status and recent events."""
    latest_reading = Reading.objects.order_by('-created_at').first()
    thresholds = get_thresholds()

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
