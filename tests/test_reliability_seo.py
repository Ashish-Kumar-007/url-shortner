from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.database import create_database
from app.main import create_app
from app.models import ClickEvent, Url
from app.services.urls import UrlService


def test_concurrent_redirects_do_not_lose_clicks(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'clicks.db').as_posix()}"
    application = create_app(database_url=database_url)
    with TestClient(application) as client:
        created = client.post(
            "/api/shorten", json={"target_url": "https://example.com"}
        ).json()

        def click(_: int):
            return client.get(f"/{created['short_code']}", follow_redirects=False)

        with ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(executor.map(click, range(16)))

        with application.state.session_factory() as session:
            UrlService(session).process_click_events()
        stats = client.get(f"/api/urls/{created['short_code']}/stats").json()

    assert all(response.status_code == 302 for response in responses)
    assert stats["click_count"] == 16


def test_validation_errors_use_the_api_error_envelope(client):
    response = client.post("/api/shorten", json={"target_url": "javascript:alert(1)"})

    assert response.status_code == 422
    assert response.headers["x-request-id"]
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["details"]


def test_private_and_credentialed_redirect_destinations_are_rejected(client):
    for destination in (
        "http://127.0.0.1/admin",
        "http://192.168.1.10/",
        "http://localhost/",
        "http://user:password@example.com/",
        "http://2130706433/",
    ):
        response = client.post("/api/shorten", json={"target_url": destination})

        assert response.status_code == 422, destination
        assert response.json()["error"]["code"] == "validation_error"


def test_legacy_private_destination_is_not_redirected_or_counted(tmp_path):
    application = create_app(
        database_url=f"sqlite:///{(tmp_path / 'legacy-private.db').as_posix()}"
    )
    with TestClient(application) as client:
        with application.state.session_factory() as session:
            session.add(Url(short_code="legacy1", target_url="http://127.0.0.1/"))
            session.commit()

        response = client.get("/legacy1", follow_redirects=False)
        with application.state.session_factory() as session:
            events = list(session.scalars(select(ClickEvent)))

    assert response.status_code == 410
    assert not events


def test_link_creation_is_rate_limited_per_client_and_returns_retry_after(tmp_path):
    settings = Settings(create_rate_limit_count=1)
    application = create_app(
        app_settings=settings,
        database_url=f"sqlite:///{(tmp_path / 'rate-limit.db').as_posix()}",
    )
    with TestClient(application) as client:
        payload = {"target_url": "https://example.com"}
        assert client.post("/api/shorten", json=payload).status_code == 201
        limited = client.post("/api/shorten", json=payload)

    assert limited.status_code == 429
    assert limited.headers["retry-after"]
    assert limited.headers["x-request-id"]
    assert limited.headers["x-content-type-options"] == "nosniff"
    assert limited.json()["error"]["code"] == "rate_limit_exceeded"


def test_trusted_proxy_client_addresses_are_used_for_rate_limits(tmp_path):
    settings = Settings(create_rate_limit_count=1, trust_proxy_headers=True)
    application = create_app(
        app_settings=settings,
        database_url=f"sqlite:///{(tmp_path / 'proxy-rate-limit.db').as_posix()}",
    )
    with TestClient(application) as client:
        payload = {"target_url": "https://example.com"}
        first = client.post(
            "/api/shorten",
            json=payload,
            headers={"X-Forwarded-For": "198.51.100.10"},
        )
        different_client = client.post(
            "/api/shorten",
            json=payload,
            headers={"X-Forwarded-For": "198.51.100.11"},
        )
        repeated_client = client.post(
            "/api/shorten",
            json=payload,
            headers={"X-Forwarded-For": "198.51.100.10"},
        )

    assert first.status_code == 201
    assert different_client.status_code == 201
    assert repeated_client.status_code == 429


def test_forwarded_ip_is_ignored_when_proxy_trust_is_disabled(tmp_path):
    settings = Settings(create_rate_limit_count=1, trust_proxy_headers=False)
    application = create_app(
        app_settings=settings,
        database_url=f"sqlite:///{(tmp_path / 'untrusted-proxy.db').as_posix()}",
    )
    with TestClient(application) as client:
        payload = {"target_url": "https://example.com"}
        first = client.post(
            "/api/shorten",
            json=payload,
            headers={"X-Forwarded-For": "198.51.100.10"},
        )
        spoofed = client.post(
            "/api/shorten",
            json=payload,
            headers={"X-Forwarded-For": "198.51.100.11"},
        )

    assert first.status_code == 201
    assert spoofed.status_code == 429


def test_security_headers_and_hsts_are_configured_for_https(tmp_path):
    settings = Settings(base_url="https://short.example")
    application = create_app(
        app_settings=settings,
        database_url=f"sqlite:///{(tmp_path / 'headers.db').as_posix()}",
    )
    with TestClient(application) as client:
        response = client.get("/health/live")

    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["strict-transport-security"].startswith("max-age=")


def test_database_failure_returns_503_and_health_stays_live(tmp_path):
    application = create_app(
        database_url=f"sqlite:///{(tmp_path / 'health.db').as_posix()}"
    )

    class BrokenSession:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, *_):
            raise OperationalError("SELECT 1", {}, Exception("database offline"))

    application.state.session_factory = BrokenSession
    with TestClient(application) as client:
        assert client.get("/health/live").status_code == 200
        readiness = client.get("/health/ready")

    assert readiness.status_code == 503
    assert readiness.json()["error"]["code"] == "database_unavailable"


def test_public_metrics_endpoint_reports_http_requests(client):
    client.get("/health")
    metrics = client.get("/metrics/")

    assert metrics.status_code == 200
    assert "url_shortener_http_requests_total" in metrics.text


def test_landing_page_has_search_metadata_and_noindex_by_default(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "URL Shortener with Click Tracking" in response.text
    assert 'name="robots" content="noindex,nofollow"' in response.text
    assert 'rel="canonical"' in response.text
    assert "click tracking" in response.text.lower()
    assert client.get("/robots.txt").text.endswith("Disallow: /\n")
    assert client.get("/sitemap.xml").status_code == 404


def test_indexable_site_publishes_canonical_sitemap_and_robots(tmp_path):
    settings = Settings(
        base_url="https://short.example",
        seo_indexable=True,
    )
    application = create_app(
        app_settings=settings,
        database_url=f"sqlite:///{(tmp_path / 'seo.db').as_posix()}",
    )
    with TestClient(application) as client:
        page = client.get("/")
        robots = client.get("/robots.txt")
        sitemap = client.get("/sitemap.xml")

    assert 'rel="canonical" href="https://short.example"' in page.text
    assert "index,follow" in page.text
    assert "Allow: /" in robots.text
    assert "https://short.example/sitemap.xml" in robots.text
    assert "<loc>https://short.example/</loc>" in sitemap.text


def test_expiration_is_enforced_with_standard_error_envelope(client):
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    response = client.post(
        "/api/shorten",
        json={"target_url": "https://example.com", "expires_at": expired},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_postgres_connection_urls_use_psycopg3():
    for scheme in ("postgres://", "postgresql://"):
        settings = Settings(
            database_url=f"{scheme}user:password@localhost:5432/shortener"
        )
        engine, _ = create_database(settings)

        assert engine.url.drivername == "postgresql+psycopg"
        engine.dispose()
