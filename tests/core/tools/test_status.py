"""The library status tool."""

from conftest import call_tool


async def test_library_status_reports_what_is_there(registry, ctx):
    status = await call_tool(registry, ctx, "library_status")
    assert status["counts"]["papers"] == 3
    assert status["semantic_search"] is True
    assert status["tier"] == "internal"
