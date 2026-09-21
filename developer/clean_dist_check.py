"""Remove a stale dist/ before tox builds a fresh one."""

import shutil
import sys
from pathlib import Path

dist = Path(__file__).resolve().parent.parent / "dist"
if dist.exists():
    shutil.rmtree(dist)
    print(f"removed {dist}")
sys.exit(0)
