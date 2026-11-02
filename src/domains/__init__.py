"""The task domains, one package each, and their registry: DOMAINS by key (the @task entry point, `--benchmark` and
the instruction levels' benchmark key) and BY_TASK by CheatBench's task name. src/configuration/task_names.py pairs
each task name with its key.

API modules:
- benchmark_protocol.py: what a domain implements, and the plan of one run.
"""
from src.configuration import task_names
from src.domains.chess import Chess
from src.domains.creative import CreativeWriting
from src.domains.interview import PrimeFactorization, SubsetSum
from src.domains.knowledge import KnowledgeWork
from src.domains.openmath import OpenMath
from src.domains.protein import ProteinDesign
from src.domains.swe import SWE

DOMAINS = {cls.key: cls for cls in (OpenMath, SWE, KnowledgeWork, SubsetSum, PrimeFactorization, ProteinDesign,
                                    CreativeWriting, Chess)}
BY_TASK = {names.task: DOMAINS[names.domain] for names in task_names.NAMES}
