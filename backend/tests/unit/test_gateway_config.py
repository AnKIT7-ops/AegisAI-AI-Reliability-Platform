"""nginx is the only way in, and its limits match what the app needs."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[3]
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")
NGINX = (ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")


def service(name: str) -> str:
    match = re.search(rf"^  {name}:\n(.*?)(?=^  \w|^\w)", COMPOSE, re.M | re.S)
    assert match, name
    return match.group(1)


def location(path: str) -> str:
    pattern = rf"location {re.escape(path)} \{{(.*?)^    \}}"
    match = re.search(pattern, NGINX, re.M | re.S)
    assert match, path
    return match.group(1)


def test_only_nginx_is_published() -> None:
    assert len(re.findall(r"^    ports:", COMPOSE, re.M)) == 1
    assert '"${FRONTEND_BIND:-127.0.0.1}:${FRONTEND_PORT:-8080}:8080"' in service(
        "frontend"
    )


def test_removed_ports_are_gone() -> None:
    for name in ("BACKEND_PORT", "PROMETHEUS_PORT", "GRAFANA_PORT"):
        assert name not in COMPOSE
        assert name not in ENV_EXAMPLE


def test_backend_sees_the_publish_address() -> None:
    assert "AEGIS_FRONTEND_BIND: ${FRONTEND_BIND:-127.0.0.1}" in service("backend")


def test_only_app_and_monitoring_paths_are_proxied() -> None:
    assert set(re.findall(r"proxy_pass \$(\w+)", NGINX)) == {
        "api",
        "grafana",
        "prometheus",
    }
    for private in ("/metrics", "/health", "/docs", "/openapi"):
        assert f"location {private}" not in NGINX


def test_body_limit_and_model_timeout() -> None:
    assert "client_max_body_size 2m;" in NGINX
    # Above AEGIS_GENERATE_TIMEOUT_S (120 s), or slow answers would become 504s.
    assert "proxy_read_timeout 180s;" in location("/api/")


def test_redirects_keep_a_remapped_port() -> None:
    assert "absolute_redirect off;" in NGINX


def test_monitoring_paths_need_the_auth_check() -> None:
    for path in ("/grafana/", "/prometheus/"):
        assert "auth_request /_auth;" in location(path)


def test_prometheus_notifications_stream_is_not_buffered() -> None:
    assert "proxy_buffering off;" in location("/prometheus/")


def test_auth_check_is_internal_and_keeps_the_host() -> None:
    block = location("= /_auth")
    assert "internal;" in block
    assert "proxy_method GET;" in block
    assert "proxy_set_header Host $http_host;" in block


def test_backend_sees_the_real_client_and_host() -> None:
    server_level = NGINX.split("    location ")[0]
    assert "proxy_set_header Host $http_host;" in server_level
    assert "proxy_set_header X-Real-IP $remote_addr;" in server_level


def test_ui_is_revalidated_after_an_upgrade() -> None:
    assert 'Cache-Control "no-cache"' in location("/")


def test_ui_responses_carry_security_headers() -> None:
    block = location("/")
    assert "Content-Security-Policy \"default-src 'self'\"" in block
    for header in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"):
        assert header in block
