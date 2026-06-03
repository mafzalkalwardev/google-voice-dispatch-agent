"""Shared pytest fixtures."""
import pytest


@pytest.fixture(autouse=True)
def _reset_groq_pool_singleton():
    import src.groq_pool as gp

    gp._pool_singleton = None
    yield
    gp._pool_singleton = None
