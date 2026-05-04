"""Integration tests for /commons/logo — run against a live surface-api at
127.0.0.1:3000 with a service-key + an existing seeded agent.

Pillow + DejaVu fonts are expected on the surface VM image. No mocks —
real renders, real wallet debits.
"""
from __future__ import annotations

import io
import os
import zipfile

import httpx
import pytest


BASE = os.environ.get("SURFACE_BASE_URL", "http://127.0.0.1:3000")
SVC_KEY = os.environ.get("SURFACE_SERVICE_KEY") or os.environ.get("SERVICE_API_KEY")
AGENT_ID = os.environ.get("SURFACE_TEST_AGENT_ID")

FIXTURE_BRAND = "KoshaPay"


def _client() -> httpx.Client:
    if not SVC_KEY or not AGENT_ID:
        pytest.skip("SURFACE_SERVICE_KEY + SURFACE_TEST_AGENT_ID required")
    return httpx.Client(
        base_url=BASE,
        headers={"X-Service-Key": SVC_KEY, "X-Agent-Id": AGENT_ID},
        timeout=60.0,
    )


def test_health_public():
    r = httpx.get(f"{BASE}/commons/logo/health", timeout=10)
    assert r.status_code == 200
    j = r.json()
    assert j["service"] == "commons.logo"
    assert "available" in j
    assert "sc_cost" in j
    assert "styles" in j
    assert set(j["styles"]) == {"minimalist", "geometric", "badge", "wordmark"}


@pytest.mark.parametrize("style", ["minimalist", "geometric", "badge", "wordmark"])
def test_generate_each_style(style):
    with _client() as c:
        r = c.post(
            "/commons/logo/generate",
            json={"brand_name": FIXTURE_BRAND, "style": style},
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "image/png"
        assert r.headers["x-style"] == style
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(r.content) > 1000


def test_generate_with_color_hex():
    with _client() as c:
        r = c.post(
            "/commons/logo/generate",
            json={"brand_name": FIXTURE_BRAND, "style": "badge", "color_hex": "#ff7a90"},
        )
        assert r.status_code == 200, r.text
        assert r.headers["x-color"] == "#ff7a90"


def test_variations_zip():
    with _client() as c:
        r = c.post(
            "/commons/logo/variations",
            json={"brand_name": FIXTURE_BRAND, "style": "geometric", "count": 4},
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/zip"
        assert r.headers["x-count"] == "4"
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = z.namelist()
        assert len(names) == 4
        assert all(n.endswith(".png") for n in names)
        # Each member is a real PNG
        for n in names:
            assert z.read(n)[:8] == b"\x89PNG\r\n\x1a\n"


def test_bad_style_rejected():
    with _client() as c:
        r = c.post(
            "/commons/logo/generate",
            json={"brand_name": FIXTURE_BRAND, "style": "psychedelic"},
        )
        assert r.status_code == 422  # pydantic Literal rejection


def test_bad_color_rejected():
    with _client() as c:
        r = c.post(
            "/commons/logo/generate",
            json={"brand_name": FIXTURE_BRAND, "style": "minimalist", "color_hex": "not-a-color"},
        )
        assert r.status_code == 422


def test_count_out_of_range():
    with _client() as c:
        r = c.post(
            "/commons/logo/variations",
            json={"brand_name": FIXTURE_BRAND, "style": "minimalist", "count": 99},
        )
        assert r.status_code == 422


def test_auth_rejected_without_key():
    r = httpx.post(
        f"{BASE}/commons/logo/generate",
        json={"brand_name": FIXTURE_BRAND, "style": "minimalist"},
        timeout=10,
    )
    assert r.status_code in (401, 422)
