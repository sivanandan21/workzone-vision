"""Pytest path configuration ensuring root, edge, and aws/lambdas are importable."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
LAMBDAS_DIR = ROOT / "aws" / "lambdas"

for p in (str(ROOT), str(LAMBDAS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)
