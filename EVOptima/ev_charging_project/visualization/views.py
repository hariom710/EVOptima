from django.shortcuts import render
import pandas as pd
import json
import os
from django.contrib.auth.decorators import login_required
from django.conf import settings
from monitoring.models import Thresholds

@login_required
def index(request):
    # Get the CSV file path relative to the project root
    BASE_DIR = settings.BASE_DIR
    # Try multiple possible locations for the CSV file
    csv_paths = [
        os.path.join(BASE_DIR.parent.parent, 'ev_charging_data2.csv'),
        os.path.join(BASE_DIR, 'data', 'ev_charging_data1.csv'),
        os.path.join(BASE_DIR.parent, 'ev_charging_data2.csv'),
    ]
    
    df = None
    for csv_path in csv_paths:
        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path)
                break
            except Exception as e:
                continue
    
    # Prepare thresholds regardless of CSV
    thresholds = Thresholds.objects.order_by('-updated_at').first()
    if thresholds is None:
        thresholds = Thresholds.objects.create()
    
    # Prepare data for JSON
    try:
        data = {
            'time': df['Time Elapsed_s'].tolist(),
            'current': df['Charging Current_A'].tolist(),
            'voltage': df['Charging Voltage_V'].tolist(),
            'power': (df['Charging Power_kW'] * 1000).tolist(),  # Convert to Watts
            'temperature': df['Battery Temperature_C'].tolist()
        }
        
        # Convert to JSON for JavaScript
        chart_data = json.dumps(data)
        
        return render(request, 'visualization/index.html', {
            'chart_data': chart_data,
            'thresholds': thresholds,
        })
    except KeyError as e:
        return render(request, 'visualization/index.html', {
            # No CSV chart data; page will still render live fault visualization
            'thresholds': thresholds,
        })
    except Exception as e:
        return render(request, 'visualization/index.html', {
            # No CSV chart data; page will still render live fault visualization
            'thresholds': thresholds,
        })
    
    # If CSV wasn't found or parsed, still render with thresholds for live charts
    return render(request, 'visualization/index.html', {
        'thresholds': thresholds,
        })