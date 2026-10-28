from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_and_create_short_link(client: TestClient):
    assert client.get("/health").json() == {"status": "ok"}
    response = client.post(
        "/api/shorten", json={"target_url": "https://example.com/path"}
    )

    assert response.status_code == 201
    data = response.json()
    assert len(data["short_code"]) == 7
    assert data["short_url"].endswith(f"/{data['short_code']}")
    assert data["target_url"] == "https://example.com/path"


def test_duplicate_urls_receive_distinct_codes(client: TestClient):
    payload = {"target_url": "https://example.com"}
    first = client.post("/api/shorten", json=payload).json()
    second = client.post("/api/shorten", json=payload).json()

    assert first["short_code"] != second["short_code"]


def test_code_collision_is_retried(client: TestClient, monkeypatch):
    codes = iter(["AAAAAAA", "AAAAAAA", "BBBBBBB"])
    monkeypatch.setattr("app.services.urls.generate_short_code", lambda _: next(codes))

    first = client.post(
        "/api/shorten", json={"target_url": "https://example.com/first"}
    )
    second = client.post(
        "/api/shorten", json={"target_url": "https://example.com/second"}
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["short_code"] == "BBBBBBB"


def test_code_collision_retries_are_bounded(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "app.services.urls.generate_short_code", lambda _: "AAAAAAA"
    )
    created = client.post(
        "/api/shorten", json={"target_url": "https://example.com/first"}
    )
    exhausted = client.post(
        "/api/shorten", json={"target_url": "https://example.com/second"}
    )

    assert created.status_code == 201
    assert exhausted.status_code == 503


def test_redirect_increments_click_count(client: TestClient):
    created = client.post(
        "/api/shorten", json={"target_url": "https://example.com"}
    ).json()

    response = client.get(f"/{created['short_code']}", follow_redirects=False)
    stats = client.get(f"/api/urls/{created['short_code']}/stats").json()

    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com/"
    assert stats["click_count"] == 1


def test_expired_and_unknown_links_return_404(client: TestClient):
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    response = client.post(
        "/api/shorten",
        json={"target_url": "https://example.com", "expires_at": expired},
    )
    assert response.status_code == 422

    link = client.post(
        "/api/shorten",
        json={
            "target_url": "https://example.com",
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(seconds=1)
            ).isoformat(),
        },
    ).json()
    import time

    time.sleep(1.1)
    assert client.get(f"/{link['short_code']}").status_code == 404
    assert client.get("/not-found").status_code == 404


def test_url_validation_rejects_non_http_schemes(client: TestClient):
    response = client.post(
        "/api/shorten", json={"target_url": "javascript:alert(1)"}
    )

    assert response.status_code == 422


def test_links_persist_after_app_restart(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'persistent.db').as_posix()}"
    first_app = create_app(database_url=database_url)
    with TestClient(first_app) as first_client:
        created = first_client.post(
            "/api/shorten", json={"target_url": "https://example.com"}
        ).json()

    second_app = create_app(database_url=database_url)
    with TestClient(second_app) as second_client:
        response = second_client.get(
            f"/{created['short_code']}", follow_redirects=False
        )

    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com/"


def test_concurrent_creations_receive_distinct_codes(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'concurrent.db').as_posix()}"
    application = create_app(database_url=database_url)
    with TestClient(application) as client:
        def create_link(index: int) -> str:
            response = client.post(
                "/api/shorten",
                json={"target_url": f"https://example.com/{index}"},
            )
            assert response.status_code == 201
            return response.json()["short_code"]

        with ThreadPoolExecutor(max_workers=8) as executor:
            codes = list(executor.map(create_link, range(16)))

    assert len(set(codes)) == 16
