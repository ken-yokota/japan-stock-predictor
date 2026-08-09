"""Database-free research helpers shared by the week-test and comparison runs.

Nothing here is part of the production morning pipeline. These modules fetch
from Yahoo directly, cache on disk, and skip the provider quality gates and
point-in-time lineage that ``data/`` enforces, so their output is a research
estimate only.
"""
