from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.urls import UrlService, generate_short_code


def test_health_and_create_short_link(client: TestClient):
    assert client.get("/health").json() == {
        "status": "ok",
        "database": "ok",
    }
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
    existing = client.post(
        "/api/shorten", json={"target_url": "https://example.com/existing"}
    ).json()["short_code"]
    available = generate_short_code()
    while available == existing:
        available = generate_short_code()
    codes = iter([existing, available])
    monkeypatch.setattr("app.services.urls.generate_short_code", lambda: next(codes))

    response = client.post(
        "/api/shorten", json={"target_url": "https://example.com/retried"}
    )

    assert response.status_code == 201
    assert response.json()["short_code"] == available


def test_code_collision_retries_are_bounded(client: TestClient, monkeypatch):
    existing = client.post(
        "/api/shorten", json={"target_url": "https://example.com/existing"}
    ).json()["short_code"]
    monkeypatch.setattr("app.services.urls.generate_short_code", lambda: existing)
    exhausted = client.post(
        "/api/shorten", json={"target_url": "https://example.com/second"}
    )

    assert exhausted.status_code == 503


def test_redirect_increments_click_count(client: TestClient):
    created = client.post(
        "/api/shorten", json={"target_url": "https://example.com"}
    ).json()

    response = client.get(f"/{created['short_code']}", follow_redirects=False)
    with client.app.state.session_factory() as session:
        UrlService(session).process_click_events()
    stats = client.get(f"/api/urls/{created['short_code']}/stats").json()

    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com/"
    assert stats["click_count"] == 1


def test_click_analytics_is_batched_and_idempotent(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'analytics.db').as_posix()}"
    application = create_app(database_url=database_url)
    with TestClient(application) as client:
        created = client.post(
            "/api/shorten", json={"target_url": "https://example.com"}
        ).json()
        for _ in range(3):
            response = client.get(f"/{created['short_code']}", follow_redirects=False)
            assert response.status_code == 302

        assert (
            client.get(f"/api/urls/{created['short_code']}/stats").json()["click_count"]
            == 0
        )
        with application.state.session_factory() as session:
            service = UrlService(session)
            assert service.process_click_events(batch_size=2) == 2
            assert service.process_click_events(batch_size=2) == 1
            assert service.process_click_events(batch_size=2) == 0
        stats = client.get(f"/api/urls/{created['short_code']}/stats").json()

    assert stats["click_count"] == 3


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
    response = client.post("/api/shorten", json={"target_url": "javascript:alert(1)"})

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
