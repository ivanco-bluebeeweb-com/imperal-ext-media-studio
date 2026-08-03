import pytest

import codes as c
import providers as p
from models import ConnectMagnificParams, NoParams


@pytest.mark.asyncio
async def test_list_providers_reports_not_connected(ctx):
    result = await p.list_providers(ctx, NoParams())
    assert result.status == "success"
    assert result.data.items[0].connected is False
    assert result.data.items[0].provider == "magnific"


@pytest.mark.asyncio
async def test_list_providers_reports_connected(ctx_with_key):
    result = await p.list_providers(ctx_with_key, NoParams())
    assert result.status == "success"
    assert result.data.items[0].connected is True


@pytest.mark.asyncio
async def test_connect_magnific_empty_key_rejected(ctx):
    result = await p.connect_magnific(ctx, ConnectMagnificParams(api_key=""))
    assert result.status == "error"
    assert result.error_code == c.MEDIA_KEY_NOT_CONFIGURED


@pytest.mark.asyncio
async def test_connect_magnific_validates_before_storing(ctx):
    ctx.http.mock_get("/v1/analytics/team-members", {"error": "unauthorized"}, status=401)
    result = await p.connect_magnific(ctx, ConnectMagnificParams(api_key="bad-key"))
    assert result.status == "error"
    assert result.error_code == c.MEDIA_PROVIDER_KEY_INVALID
    assert await ctx.secrets.is_set("magnific_api_key") is False


@pytest.mark.asyncio
async def test_connect_magnific_stores_valid_key(ctx):
    ctx.http.mock_get("/v1/analytics/team-members", {"data": []}, status=200)
    result = await p.connect_magnific(ctx, ConnectMagnificParams(api_key="good-key"))
    assert result.status == "success"
    assert result.data.connected is True
    assert await ctx.secrets.get("magnific_api_key") == "good-key"


@pytest.mark.asyncio
async def test_disconnect_magnific_removes_key(ctx_with_key):
    result = await p.disconnect_magnific(ctx_with_key, NoParams())
    assert result.status == "success"
    assert await ctx_with_key.secrets.is_set("magnific_api_key") is False


@pytest.mark.asyncio
async def test_disconnect_magnific_when_not_connected(ctx):
    result = await p.disconnect_magnific(ctx, NoParams())
    assert result.status == "success"
    assert result.data.connected is False
