"""conftest.py — Add src/ to sys.path so weatherbet package is importable."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
