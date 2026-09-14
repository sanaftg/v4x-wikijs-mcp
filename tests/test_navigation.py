import importlib
import os
import sys

import pytest

os.environ.setdefault("WIKIJS_API_KEY", "test-key")
sys.path.insert(0, "src")
server = importlib.import_module("v4x_wikijs_mcp")


def navigation_state():
    return {
        "mode": "TREE",
        "trees": [
            {
                "locale": "en",
                "items": [
                    {
                        "id": "en-home",
                        "kind": "link",
                        "label": "Home",
                        "icon": "mdi-home",
                        "targetType": "home",
                        "target": "",
                        "visibilityMode": "all",
                        "visibilityGroups": [],
                    }
                ],
            },
            {"locale": "ja", "items": []},
        ],
    }


def test_navigation_version_is_stable_for_key_order():
    first = {"mode": "STATIC", "trees": [{"locale": "ja", "items": []}]}
    second = {"trees": [{"items": [], "locale": "ja"}], "mode": "STATIC"}

    assert server.navigation_version(first) == server.navigation_version(second)


def test_normalize_navigation_items_validates_page_path():
    items = server.normalize_navigation_items(
        [
            {
                "id": "development",
                "kind": "link",
                "label": "開発",
                "targetType": "page",
                "target": "/ja/development",
            }
        ],
        "ja",
    )

    assert items[0]["target"] == "/ja/development"
    assert items[0]["icon"] == "mdi-chevron-right"


def test_normalize_navigation_items_rejects_duplicate_ids():
    with pytest.raises(ValueError, match="unique"):
        server.normalize_navigation_items(
            [
                {"id": "same", "kind": "header", "label": "One"},
                {"id": "same", "kind": "header", "label": "Two"},
            ],
            "ja",
        )


def test_normalize_navigation_item_rejects_wrong_page_locale():
    with pytest.raises(ValueError, match="/ja/<page-path>"):
        server.normalize_navigation_item(
            {
                "id": "wrong-locale",
                "kind": "link",
                "label": "Wrong locale",
                "targetType": "page",
                "target": "/en/development",
            },
            "ja",
        )


def test_normalize_navigation_item_rejects_unsafe_external_target():
    with pytest.raises(ValueError, match="absolute HTTP"):
        server.normalize_navigation_item(
            {
                "id": "unsafe",
                "kind": "link",
                "label": "Unsafe",
                "targetType": "external",
                "target": "javascript:alert(1)",
            },
            "ja",
        )


def test_replace_navigation_tree_preserves_other_locales():
    result = server.replace_navigation_tree(
        navigation_state()["trees"],
        "ja",
        [{"id": "ja-home"}],
    )

    assert result[0]["locale"] == "en"
    assert result[0]["items"][0]["id"] == "en-home"
    assert result[1] == {"locale": "ja", "items": [{"id": "ja-home"}]}


@pytest.mark.asyncio
async def test_update_navigation_dry_run_does_not_mutate(monkeypatch):
    state = navigation_state()
    requests = []

    async def fake_get_navigation_state():
        return state

    async def fake_request(query, variables=None):
        requests.append((query, variables))
        raise AssertionError("dry-run must not send a mutation")

    monkeypatch.setattr(server, "get_navigation_state", fake_get_navigation_state)
    monkeypatch.setattr(server.wiki, "request", fake_request)

    result = await server.wikijs_update_navigation.fn(
        items=[{"id": "section", "kind": "header", "label": "開発"}],
        expected_version=server.navigation_version(state),
        locale="ja",
        mode="STATIC",
    )

    assert result["dryRun"] is True
    assert result["modeBefore"] == "TREE"
    assert result["modeAfter"] == "STATIC"
    assert result["itemCountAfter"] == 1
    assert requests == []


@pytest.mark.asyncio
async def test_update_navigation_rejects_stale_version(monkeypatch):
    async def fake_get_navigation_state():
        return navigation_state()

    monkeypatch.setattr(server, "get_navigation_state", fake_get_navigation_state)

    with pytest.raises(RuntimeError, match="changed after it was read"):
        await server.wikijs_update_navigation.fn(
            items=[],
            expected_version="stale",
            locale="ja",
        )


@pytest.mark.asyncio
async def test_update_navigation_writes_all_locale_trees(monkeypatch):
    state = navigation_state()
    captured = {}

    async def fake_get_navigation_state():
        return state

    async def fake_request(query, variables=None):
        captured["query"] = query
        captured["variables"] = variables
        return {
            "navigation": {
                "updateTree": {"responseResult": {"succeeded": True, "message": "ok"}},
                "updateConfig": {
                    "responseResult": {"succeeded": True, "message": "ok"}
                },
            }
        }

    monkeypatch.setattr(server, "get_navigation_state", fake_get_navigation_state)
    monkeypatch.setattr(server.wiki, "request", fake_request)

    result = await server.wikijs_update_navigation.fn(
        items=[
            {
                "id": "development",
                "kind": "link",
                "label": "開発",
                "targetType": "page",
                "target": "/ja/development",
            }
        ],
        expected_version=server.navigation_version(state),
        locale="ja",
        mode="STATIC",
        dry_run=False,
    )

    assert result["succeeded"] is True
    assert captured["variables"]["mode"] == "STATIC"
    assert [tree["locale"] for tree in captured["variables"]["tree"]] == [
        "en",
        "ja",
    ]
    assert captured["variables"]["tree"][0]["items"][0]["id"] == "en-home"
