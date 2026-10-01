import pytest

from suraksha.store.memory import MemoryStore


@pytest.fixture
def store():
    return MemoryStore()
