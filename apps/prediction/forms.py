"""The prediction form.

Voltage and current were added when the models were retrained. Previously the
view hard-coded ``Charging Power_kW = 50`` as a model *input* while the training
data spanned 1.0-7.0 kW -- a z-score of +26.6, fourteen times outside anything
the model had seen, and invisible to the operator because no field exposed it.
Power is now a model *output* derived from the electrical inputs below.

``voltage`` and ``current`` are optional so a submission that omits them (an
API caller, an older bookmark, the test suite) still resolves to a value inside
the training distribution rather than failing validation.
"""
from django import forms

#: Fallbacks for omitted electrical inputs. Both sit inside the combined
#: training range recorded in ``meta.json`` (``Charging Voltage_V`` 200-800,
#: ``Charging Current_A`` 2-166) and yield a predicted current comfortably
#: within the default fault thresholds (10-30 A).
DEFAULT_VOLTAGE_V = 400.0
DEFAULT_CURRENT_A = 15.0

#: Training distribution, with headroom. Values outside these are rejected by
#: form validation; values inside but outside ``meta.json``'s tighter ranges
#: raise an out-of-distribution *warning* event rather than a hard error.
VOLTAGE_LIMITS = (150.0, 900.0)
CURRENT_LIMITS = (0.0, 250.0)
TEMPERATURE_LIMITS = (0.0, 60.0)
SOC_LIMITS = (0, 100)
DURATION_LIMITS = (0.0, 24.0)


class PredictionForm(forms.Form):
    voltage = forms.FloatField(
        label="Charging Voltage (V)",
        required=False,
        min_value=VOLTAGE_LIMITS[0],
        max_value=VOLTAGE_LIMITS[1],
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "any", "placeholder": f"default {DEFAULT_VOLTAGE_V:.0f}"}
        ),
    )
    current = forms.FloatField(
        label="Charging Current (A)",
        required=False,
        min_value=CURRENT_LIMITS[0],
        max_value=CURRENT_LIMITS[1],
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "any", "placeholder": f"default {DEFAULT_CURRENT_A:.0f}"}
        ),
    )
    battery_temp = forms.FloatField(
        label="Battery Temperature (\u00b0C)",
        min_value=TEMPERATURE_LIMITS[0],
        max_value=TEMPERATURE_LIMITS[1],
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "any"}),
    )
    soc = forms.FloatField(
        label="State of Charge (%)",
        min_value=SOC_LIMITS[0],
        max_value=SOC_LIMITS[1],
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "any"}),
    )
    duration = forms.FloatField(
        label="Charging Duration (hours)",
        min_value=DURATION_LIMITS[0],
        max_value=DURATION_LIMITS[1],
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "any"}),
    )
    timestamp = forms.DateTimeField(
        label="Charging Time",
        widget=forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}),
    )

    def clean_voltage(self):
        value = self.cleaned_data.get("voltage")
        return DEFAULT_VOLTAGE_V if value is None else value

    def clean_current(self):
        value = self.cleaned_data.get("current")
        return DEFAULT_CURRENT_A if value is None else value
