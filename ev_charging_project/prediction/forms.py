from django import forms

class PredictionForm(forms.Form):
    charging_power = forms.FloatField(
        label='Charging Power (kW)',
        min_value=0,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    battery_temp = forms.FloatField(
        label='Battery Temperature (°C)',
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    soc = forms.FloatField(
        label='State of Charge (%)',
        min_value=0,
        max_value=100,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    duration = forms.FloatField(
        label='Charging Duration (hours)',
        min_value=0,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    timestamp = forms.DateTimeField(
        label='Charging Time',
        widget=forms.DateTimeInput(attrs={
            'class': 'form-control',
            'type': 'datetime-local'
        })
    )