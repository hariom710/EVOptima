from django.db import models

# Create your models here.


class Thresholds(models.Model):
    # EV Charger Standard Limits - Updated per user requirements
    min_current = models.FloatField(default=10.0)   # Minimum 10A for safe charging
    max_current = models.FloatField(default=30.0)   # Maximum 30A for safe charging
    min_voltage = models.FloatField(default=400.0)  # Minimum 400V for DC fast charging
    max_voltage = models.FloatField(default=460.0)  # Maximum 460V for DC fast charging
    min_temperature = models.FloatField(default=0.0)   # Minimum 0°C operating temperature
    max_temperature = models.FloatField(default=80.0)  # Maximum 80°C operating temperature
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Thresholds({self.min_current}-{self.max_current}A, {self.min_voltage}-{self.max_voltage}V, {self.min_temperature}-{self.max_temperature}°C)"


class Reading(models.Model):
    current = models.FloatField()
    voltage = models.FloatField()
    temperature = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Reading I={self.current}A V={self.voltage}V T={self.temperature}C @ {self.created_at}"


class EventLog(models.Model):
    EVENT_TYPES = (
        ('FAULT_DETECTED', 'Fault Detected'),
        ('CHARGING_STOPPED', 'Charging Stopped'),
        ('THRESHOLDS_CHANGED', 'Thresholds Changed'),
        ('INFO', 'Info'),
        ('PREDICTION_ERROR', 'Prediction Error'),
    )

    event_type = models.CharField(max_length=32, choices=EVENT_TYPES)
    details = models.TextField(blank=True)
    response = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.event_type} @ {self.created_at}: {self.details[:50]}"


