"""Shared utilities for the FastAPI application and Celery workers.

This package holds small, dependency-free helpers that are reused
across modules.  Keep these utilities pure and side-effect-free so
they can be imported from any layer (API, tasks, RAG) without
introducing a cycle.
"""
