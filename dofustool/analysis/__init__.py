"""Analyse : prix de référence, marges de craft, liquidité, tendances."""
from ..db import GRAIN_DAY, GRAIN_HOUR

__all__ = ["DAY", "GRAIN_DAY", "GRAIN_HOUR", "HOUR"]

HOUR = 3600.0
DAY = 86400.0
