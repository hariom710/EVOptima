"""Site electrical constants shared by everything that reports on the bus.

Hoisted out of ``apps.prediction`` because two independent consumers need the
same number and a divergence would be invisible: the prediction page renders
the allocation visual against this budget, and the scheduler treats it as the
capacity row of its LP. One definition, one value to change when the site
upgrade lands.
"""
from __future__ import annotations

#: Main DC bus budget in kW -- the ceiling on simultaneous charging across
#: every port. Serves as the allocation limit on the prediction page and as
#: the ``capacity_kw`` default for ``optimization.solver.solve_schedule``.
TOTAL_POWER_KW = 100.0
