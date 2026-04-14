#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weatherbet.py — Weather Trading Bot for Polymarket
=====================================================
Tracks weather forecasts from 3 sources (ECMWF, HRRR, METAR),
compares with Polymarket markets, paper trades using Kelly criterion.

Usage:
    python weatherbet.py          # main loop
    python weatherbet.py report   # full report
    python weatherbet.py status   # balance and open positions
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from weatherbet.cli import main

if __name__ == "__main__":
    main()
