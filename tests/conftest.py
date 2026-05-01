import pytest

pytest_plugins = ["pytest_asyncio"]


@pytest.fixture(scope="session")
def event_loop_policy():
    import asyncio
    return asyncio.get_event_loop_policy()
