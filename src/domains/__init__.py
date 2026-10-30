"""The task domains, one package each, and their registry: DOMAINS by key (the @task entry point, `--benchmark` and
the instruction levels' benchmark key) and BY_TASK by CheatBench's task name. What a domain implements is
src/domains/benchmark_protocol.py."""
from src.domains.interview import PrimeFactorization, SubsetSum
from src.domains.knowledge import KnowledgeWork
from src.domains.openmath import OpenMath
from src.domains.protein import ProteinDesign
from src.domains.swe import SWE

DOMAINS = {cls.key: cls for cls in (OpenMath, SWE, KnowledgeWork, SubsetSum, PrimeFactorization, ProteinDesign)}
BY_TASK = {cls.name: cls for cls in DOMAINS.values()}
