import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path) -> Generator[TestClient, None, None]:
    database_url = os.getenv("TEST_DATABASE_URL") or (
        f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    )
    application = create_app(database_url=database_url)
    with TestClient(application) as test_client:
        yield test_client
