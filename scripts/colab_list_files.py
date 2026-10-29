#!/usr/bin/env python3
import os
from pathlib import Path
for p in sorted(Path("/content/weft/benchmarks").glob("*")):
    print(f"{p.stat().st_size:>12}  {p.name}")
