import io

import pytest
from httpx import AsyncClient


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:56493",
        "http://127.0.0.1:56493",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
)
@pytest.mark.asyncio
async def test_auth_register_preflight_allows_development_origins(
    client: AsyncClient, origin: str
) -> None:
    response = await client.options(
        "/api/v1/auth/register",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "POST" in response.headers["access-control-allow-methods"]


@pytest.mark.asyncio
async def test_register_login_and_authenticated_me(client: AsyncClient) -> None:
    origin = "http://localhost:56493"
    register = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Amina User",
            "email": "AMINA@example.com",
            "password": "a-secure-password-123",
            "preferred_language": "en",
        },
        headers={"Origin": origin},
    )
    assert register.status_code == 201
    assert register.headers["access-control-allow-origin"] == origin
    assert register.json()["email"] == "amina@example.com"
    assert "hashed_password" not in register.json()

    duplicate = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Amina User",
            "email": "amina@example.com",
            "password": "a-secure-password-123",
        },
    )
    assert duplicate.status_code == 409

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "amina@example.com", "password": "a-secure-password-123"},
        headers={"Origin": origin},
    )
    assert login.status_code == 200
    assert login.headers["access-control-allow-origin"] == origin
    tokens = login.json()
    assert tokens["access_token"]
    assert tokens["refresh_token"]

    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["id"] == register.json()["id"]

    refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    rejected = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert rejected.status_code == 401
    report = await client.post(
        "/api/v1/reports/",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        json={"report_type": "OTHER", "description": "Please review this catalog entry."},
    )
    assert report.status_code == 201
    history = await client.get(
        "/api/v1/reports/history",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert len(history.json()) == 1


@pytest.mark.asyncio
async def test_product_and_guest_scan_analysis_remain_unknown(client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/products/",
        json={"barcode": "123456789012", "name": "Oat Snack", "brand": "Example"},
    )
    assert created.status_code == 201
    found = await client.get("/api/v1/products/123456789012")
    assert found.status_code == 200
    searched = await client.get("/api/v1/products/search", params={"q": "Oat"})
    assert searched.status_code == 200
    assert len(searched.json()) == 1

    scan = await client.post("/api/v1/scans/barcode", json={"barcode": "123456789012"})
    assert scan.status_code == 201
    assert scan.json()["product_id"] == created.json()["id"]
    analyzed = await client.post(
        "/api/v1/scans/analyze",
        json={"scan_id": scan.json()["id"], "original_ingredients_text": "glycerin"},
    )
    assert analyzed.status_code == 200
    assert analyzed.json()["overall_status"] == "DOUBTFUL"


@pytest.mark.asyncio
async def test_image_scan_validates_and_accepts_image_placeholder(client: AsyncClient) -> None:
    invalid = await client.post(
        "/api/v1/scans/image",
        files={"image": ("notes.txt", io.BytesIO(b"not an image"), "text/plain")},
    )
    assert invalid.status_code == 415

    accepted = await client.post(
        "/api/v1/scans/image",
        files={
            "image": (
                "label.png",
                io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"placeholder"),
                "image/png",
            )
        },
    )
    assert accepted.status_code == 201
    assert accepted.json()["image_url"] is None


@pytest.mark.asyncio
async def test_image_scan_rejects_mismatched_content(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/scans/image",
        files={"image": ("label.png", io.BytesIO(b"not png content"), "image/png")},
    )
    assert response.status_code == 422
