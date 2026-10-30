"""CheatBench's SWE issues with Lucy's honeypots, as a Benchmark.

API modules:
- swe_task.py: the SWE Benchmark.
- swe_inputs.py: Lucy's split of each issue and CheatBench's task folder, read only.
- swe_ladder.py: the code ladder's rules for SWE.
- swe_sandbox.py: the images, the compose file, each agent's seat and tools, and the setup and grader commands.
- swe_settings.py: the domain's fixed paths, sandbox, honeypots and split.
"""
from src.domains.swe.swe_task import SWE

__all__ = ["SWE"]
