"""Historical charging-data charts plus the live fault-simulation view."""
import json
import logging

import pandas as pd
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.monitoring.services import get_thresholds

logger = logging.getLogger(__name__)

#: Candidate datasets, most preferred first.
CSV_PATHS = (
    "ev_charging_data2.csv",
    "ev_charging_data1.csv",
    "ev_charging_data.csv",
)


@login_required
def index(request):
    """Render the charts page.

    Chart data is optional: when no usable dataset is present the page still
    renders with live thresholds so the fault visualisation keeps working.
    """
    thresholds = get_thresholds()

    context = {"thresholds": thresholds}

    df = _load_dataset()
    if df is not None:
        try:
            context["chart_data"] = json.dumps(
                {
                    "time": df["Time Elapsed_s"].tolist(),
                    "current": df["Charging Current_A"].tolist(),
                    "voltage": df["Charging Voltage_V"].tolist(),
                    "power": (df["Charging Power_kW"] * 1000).tolist(),  # watts
                    "temperature": df["Battery Temperature_C"].tolist(),
                }
            )
        except KeyError as exc:
            logger.warning("Dataset is missing expected column: %s", exc)
    else:
        logger.info("No charging dataset found; rendering live-only view")

    return render(request, "visualization/index.html", context)


def _load_dataset() -> pd.DataFrame | None:
    """Locate and read the first readable dataset, or return ``None``."""
    candidates = [settings.DATA_DIR / name for name in CSV_PATHS]
    candidates.append(settings.BASE_DIR / "data" / "samples" / "ev_sample.csv")

    for csv_path in candidates:
        if not csv_path.exists():
            continue
        try:
            return pd.read_csv(csv_path)
        except Exception as exc:  # noqa: BLE001 - one bad file must not break the page
            logger.warning("Could not read %s: %s", csv_path, exc)
    return None
