import importlib
import os
import sys

import pytest


os.environ.setdefault("WIKIJS_API_KEY", "test-key")
sys.path.insert(0, "src")
server = importlib.import_module("v4x_wikijs_mcp")


def test_destination_for_path_replaces_only_tree_root():
    assert (
        server.destination_for_path(
            "開発環境/steampipe",
            "開発環境",
            "開発/開発環境",
        )
        == "開発/開発環境/steampipe"
    )


@pytest.mark.asyncio
async def test_plan_page_tree_move_orders_descendants_first(monkeypatch):
    async def fake_list_pages():
        return [
            {
                "id": 1,
                "path": "開発環境",
                "title": "開発環境",
                "locale": "ja",
                "updatedAt": "root-version",
            },
            {
                "id": 2,
                "path": "開発環境/steampipe",
                "title": "SteamPipe",
                "locale": "ja",
                "updatedAt": "child-version",
            },
            {
                "id": 3,
                "path": "other",
                "title": "Other",
                "locale": "ja",
                "updatedAt": "other-version",
            },
        ]

    monkeypatch.setattr(server, "wikijs_list_pages", fake_list_pages)

    plan = await server.plan_page_tree_move(
        "開発環境",
        "開発/開発環境",
        "ja",
    )

    assert [item["sourcePath"] for item in plan] == [
        "開発環境/steampipe",
        "開発環境",
    ]
    assert [item["destinationPath"] for item in plan] == [
        "開発/開発環境/steampipe",
        "開発/開発環境",
    ]


@pytest.mark.asyncio
async def test_plan_page_tree_move_rejects_existing_destination(monkeypatch):
    async def fake_list_pages():
        return [
            {
                "id": 1,
                "path": "開発環境",
                "title": "開発環境",
                "locale": "ja",
                "updatedAt": "root-version",
            },
            {
                "id": 2,
                "path": "開発/開発環境",
                "title": "Existing",
                "locale": "ja",
                "updatedAt": "existing-version",
            },
        ]

    monkeypatch.setattr(server, "wikijs_list_pages", fake_list_pages)

    with pytest.raises(ValueError, match="Destination page already exists"):
        await server.plan_page_tree_move(
            "開発環境",
            "開発/開発環境",
            "ja",
        )


@pytest.mark.asyncio
async def test_plan_page_tree_move_rejects_moving_inside_itself(monkeypatch):
    async def fake_list_pages():
        return []

    monkeypatch.setattr(server, "wikijs_list_pages", fake_list_pages)

    with pytest.raises(ValueError, match="inside itself"):
        await server.plan_page_tree_move(
            "開発環境",
            "開発環境/archive",
            "ja",
        )
