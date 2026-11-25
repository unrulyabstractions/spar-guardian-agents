"""CheatBench's protein_design task for one target, as a Benchmark (README.md).

The package exports ProteinDesign. Two modules are public besides: protein_task.py, which holds it, and
protein_rows_build.py, the build of the rows into build/protein_design/, which the box runner calls. Every other
module is private to the package, its tests and its replay (src/replay/protein_replay.py).
"""
from src.domains.protein.protein_task import ProteinDesign

__all__ = ["ProteinDesign"]
