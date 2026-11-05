#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 Nolan Guo
"""Run each bank suite in a fresh process to isolate bank-local modules."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BANKS = ("bangkok-bank", "kbank", "scb", "lh-bank")

def main():
    for bank in BANKS:
        print(f"Testing {bank}", flush=True)
        result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT / "th" / bank)
        if result.returncode:
            return result.returncode
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
