"""CheatBench's protein_design task for one target, as a Benchmark.

API modules:
- protein_task.py: the ProteinDesign Benchmark.
- protein_rows_build.py: the rows, built into build/protein_design/ from CheatBench's generator.
"""
from src.domains.protein.protein_task import ProteinDesign

__all__ = ["ProteinDesign"]
