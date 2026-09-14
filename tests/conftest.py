from __future__ import annotations

import sys
from pathlib import Path


I22_DIR = Path(__file__).parents[1] / "DLS" / "I22"
sys.path.insert(0, str(I22_DIR))
