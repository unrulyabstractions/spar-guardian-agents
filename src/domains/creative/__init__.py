"""CheatBench's creative_writings task for one brief, as a Benchmark (README.md in this folder).

CreativeWriting is the package's interface. creative_task.py and creative_sandbox.py are public too, since the replay,
the box runner and a paper figure import them; every other module is private.
"""
from src.domains.creative.creative_task import CreativeWriting

__all__ = ["CreativeWriting"]
