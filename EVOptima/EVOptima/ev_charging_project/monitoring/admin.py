from django.contrib import admin
from .models import Thresholds, Reading, EventLog


@admin.register(Thresholds)
class ThresholdsAdmin(admin.ModelAdmin):
	list_display = ('min_current', 'max_current', 'min_voltage', 'max_voltage', 'max_temperature', 'updated_at')


@admin.register(Reading)
class ReadingAdmin(admin.ModelAdmin):
	list_display = ('current', 'voltage', 'temperature', 'created_at')
	ordering = ('-created_at',)


@admin.register(EventLog)
class EventLogAdmin(admin.ModelAdmin):
	list_display = ('event_type', 'created_at', 'details', 'response')
	ordering = ('-created_at',)







