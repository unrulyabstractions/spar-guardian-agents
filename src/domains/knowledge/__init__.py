"""CheatBench's knowledge_work task for one GDPval row, as a Benchmark.

API modules:
- knowledge_task.py: the KnowledgeWork Benchmark.
- knowledge_sandbox.py: the images, the compose file, the seats, the staging tarball and the setup script.
- knowledge_settings.py: the domain's fixed paths, images, groups and limits.
"""
from src.domains.knowledge.knowledge_task import KnowledgeWork

__all__ = ["KnowledgeWork"]
