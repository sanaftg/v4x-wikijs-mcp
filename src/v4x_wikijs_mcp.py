#!/usr/bin/env python3
"""Restricted Wiki.js MCP server for the V4X documentation."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastmcp import FastMCP
from pydantic_settings import BaseSettings, SettingsConfigDict
from slugify import slugify


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env.v4x", extra="ignore")

    WIKIJS_API_URL: str = "http://host.docker.internal:3000"
    WIKIJS_API_KEY: str
    WIKIJS_ALLOWED_PATH_PREFIXES: str = "*"
    WIKIJS_DEFAULT_LOCALE: str = "ja"
    MCP_HOST: str = "0.0.0.0"
    MCP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"


settings = Settings()
logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
logger = logging.getLogger("v4x-wikijs-mcp")

mcp = FastMCP(
    "V4X Wiki.js",
    instructions=(
        "Search and read V4X documentation before editing it. "
        "Create or update pages only when the user explicitly requests a write. "
        "Never invent page identifiers or paths."
    ),
)


def normalize_path(path: str) -> str:
    return path.strip().strip("/")


def allowed_prefixes() -> tuple[str, ...]:
    values = tuple(
        normalize_path(value)
        for value in settings.WIKIJS_ALLOWED_PATH_PREFIXES.split(",")
        if value.strip()
    )
    return values or ("*",)


def is_allowed_path(path: str) -> bool:
    normalized = normalize_path(path)
    if not normalized:
        return False
    prefixes = allowed_prefixes()
    return "*" in prefixes or any(
        normalized == prefix or normalized.startswith(prefix + "/")
        for prefix in prefixes
    )


def require_allowed_path(path: str) -> str:
    normalized = normalize_path(path)
    if not is_allowed_path(normalized):
        raise ValueError(
            f"Page path is outside the allowed Wiki.js paths: {allowed_prefixes()}."
        )
    return normalized


class WikiJSClient:
    def __init__(self) -> None:
        self.url = settings.WIKIJS_API_URL.rstrip("/") + "/graphql"

    async def request(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {settings.WIKIJS_API_KEY}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            response = await client.post(
                self.url,
                headers=headers,
                json={"query": query, "variables": variables or {}},
            )
            response.raise_for_status()
            payload = response.json()

        if payload.get("errors"):
            messages = "; ".join(error.get("message", str(error)) for error in payload["errors"])
            raise RuntimeError(f"Wiki.js GraphQL error: {messages}")
        return payload.get("data", {})


wiki = WikiJSClient()


async def get_page_by_path(path: str, locale: str | None = None) -> dict[str, Any]:
    safe_path = require_allowed_path(path)
    query = """
    query($path: String!, $locale: String!) {
      pages {
        singleByPath(path: $path, locale: $locale) {
          id path title description content isPrivate isPublished locale
          tags { tag }
          createdAt updatedAt
        }
      }
    }
    """
    data = await wiki.request(
        query,
        {"path": safe_path, "locale": locale or settings.WIKIJS_DEFAULT_LOCALE},
    )
    page = data.get("pages", {}).get("singleByPath")
    if not page:
        raise ValueError(f"Page not found: {safe_path}")
    require_allowed_path(page["path"])
    return page


@mcp.tool()
async def wikijs_connection_status() -> dict[str, Any]:
    """Check whether the restricted MCP server can reach Wiki.js."""
    query = "query { pages { list { id path } } }"
    data = await wiki.request(query)
    return {
        "connected": True,
        "accessiblePageCount": len(data.get("pages", {}).get("list", [])),
        "allowedPathPrefixes": list(allowed_prefixes()),
        "defaultLocale": settings.WIKIJS_DEFAULT_LOCALE,
    }


async def list_pages() -> list[dict[str, Any]]:
    """Fetch page metadata under the configured V4X path."""
    query = """
    query {
      pages {
        list {
          id path title description locale isPublished isPrivate updatedAt
        }
      }
    }
    """
    data = await wiki.request(query)
    pages = data.get("pages", {}).get("list", [])
    return [page for page in pages if is_allowed_path(page.get("path", ""))]


@mcp.tool()
async def wikijs_list_pages() -> list[dict[str, Any]]:
    """List page metadata under the configured V4X path only."""
    return await list_pages()


@mcp.tool()
async def wikijs_search_pages(query: str) -> list[dict[str, Any]]:
    """Search V4X page titles, paths, descriptions, and content."""
    term = query.strip().casefold()
    if not term:
        return []

    matches: list[dict[str, Any]] = []
    for metadata in await list_pages():
        page = await get_page_by_path(metadata["path"], metadata.get("locale"))
        haystack = "\n".join(
            str(page.get(key, "")) for key in ("title", "path", "description", "content")
        ).casefold()
        if term in haystack:
            matches.append({
                "id": page["id"],
                "path": page["path"],
                "title": page["title"],
                "description": page.get("description", ""),
                "locale": page.get("locale"),
                "updatedAt": page.get("updatedAt"),
            })
    return matches


@mcp.tool()
async def wikijs_get_page(path: str, locale: str | None = None) -> dict[str, Any]:
    """Read one page under the configured V4X path."""
    return await get_page_by_path(path, locale)


@mcp.tool()
async def wikijs_create_page(
    title: str,
    content: str,
    path: str | None = None,
    description: str = "",
    tags: list[str] | None = None,
    locale: str | None = None,
    is_private: bool = True,
) -> dict[str, Any]:
    """Create a Markdown page under the V4X path. This is a write action."""
    prefixes = allowed_prefixes()
    if path:
        requested_path = path
    elif "*" in prefixes:
        requested_path = slugify(title)
    else:
        requested_path = f"{prefixes[0]}/{slugify(title)}"
    safe_path = require_allowed_path(requested_path)
    page_locale = locale or settings.WIKIJS_DEFAULT_LOCALE

    mutation = """
    mutation(
      $content: String!, $description: String!, $editor: String!,
      $isPublished: Boolean!, $isPrivate: Boolean!, $locale: String!,
      $path: String!, $publishEndDate: Date, $publishStartDate: Date,
      $scriptCss: String, $scriptJs: String, $tags: [String]!, $title: String!
    ) {
      pages {
        create(
          content: $content, description: $description, editor: $editor,
          isPublished: $isPublished, isPrivate: $isPrivate, locale: $locale,
          path: $path, publishEndDate: $publishEndDate,
          publishStartDate: $publishStartDate, scriptCss: $scriptCss,
          scriptJs: $scriptJs, tags: $tags, title: $title
        ) {
          responseResult { succeeded errorCode message }
          page { id path title createdAt updatedAt }
        }
      }
    }
    """
    variables = {
        "content": content,
        "description": description,
        "editor": "markdown",
        "isPublished": True,
        "isPrivate": is_private,
        "locale": page_locale,
        "path": safe_path,
        "publishEndDate": None,
        "publishStartDate": None,
        "scriptCss": "",
        "scriptJs": "",
        "tags": tags or [],
        "title": title,
    }
    result = (await wiki.request(mutation, variables)).get("pages", {}).get("create", {})
    response_result = result.get("responseResult", {})
    if not response_result.get("succeeded"):
        raise RuntimeError(response_result.get("message", "Wiki.js page creation failed."))
    return result.get("page", {})


@mcp.tool()
async def wikijs_update_page(
    path: str,
    content: str,
    expected_updated_at: str,
    title: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Replace a V4X Markdown page after checking its updatedAt value. This is a write action."""
    current = await get_page_by_path(path)
    if current.get("updatedAt") != expected_updated_at:
        raise RuntimeError(
            "The page changed after it was read. Read it again before updating."
        )

    mutation = """
    mutation(
      $id: Int!, $content: String!, $description: String!, $editor: String!,
      $isPrivate: Boolean!, $isPublished: Boolean!, $locale: String!,
      $path: String!, $scriptCss: String, $scriptJs: String,
      $tags: [String]!, $title: String!
    ) {
      pages {
        update(
          id: $id, content: $content, description: $description, editor: $editor,
          isPrivate: $isPrivate, isPublished: $isPublished, locale: $locale,
          path: $path, scriptCss: $scriptCss, scriptJs: $scriptJs,
          tags: $tags, title: $title
        ) {
          responseResult { succeeded errorCode message }
          page { id path title updatedAt }
        }
      }
    }
    """
    variables = {
        "id": current["id"],
        "content": content,
        "description": current.get("description", "") if description is None else description,
        "editor": "markdown",
        "isPrivate": current.get("isPrivate", True),
        "isPublished": current.get("isPublished", True),
        "locale": current.get("locale", settings.WIKIJS_DEFAULT_LOCALE),
        "path": require_allowed_path(current["path"]),
        "scriptCss": "",
        "scriptJs": "",
        "tags": [item["tag"] for item in current.get("tags", [])],
        "title": current["title"] if title is None else title,
    }
    result = (await wiki.request(mutation, variables)).get("pages", {}).get("update", {})
    response_result = result.get("responseResult", {})
    if not response_result.get("succeeded"):
        raise RuntimeError(response_result.get("message", "Wiki.js page update failed."))
    return result.get("page", {})


async def move_page_to_path(
    page: dict[str, Any],
    destination_path: str,
    expected_updated_at: str,
) -> dict[str, Any]:
    """Move one previously-read page while preserving its content and metadata."""
    if page.get("updatedAt") != expected_updated_at:
        raise RuntimeError(
            "The page changed after it was read. Read it again before moving."
        )

    source_path = require_allowed_path(page["path"])
    safe_destination = require_allowed_path(destination_path)
    if source_path == safe_destination:
        raise ValueError("Source and destination paths are the same.")

    current = await get_page_by_path(source_path, page.get("locale"))
    if current.get("updatedAt") != expected_updated_at:
        raise RuntimeError(
            "The page changed after it was read. Read it again before moving."
        )

    mutation = """
    mutation(
      $id: Int!, $content: String!, $description: String!, $editor: String!,
      $isPrivate: Boolean!, $isPublished: Boolean!, $locale: String!,
      $path: String!, $scriptCss: String, $scriptJs: String,
      $tags: [String]!, $title: String!
    ) {
      pages {
        update(
          id: $id, content: $content, description: $description, editor: $editor,
          isPrivate: $isPrivate, isPublished: $isPublished, locale: $locale,
          path: $path, scriptCss: $scriptCss, scriptJs: $scriptJs,
          tags: $tags, title: $title
        ) {
          responseResult { succeeded errorCode message }
          page { id path title updatedAt }
        }
      }
    }
    """
    variables = {
        "id": current["id"],
        "content": current.get("content", ""),
        "description": current.get("description", ""),
        "editor": "markdown",
        "isPrivate": current.get("isPrivate", True),
        "isPublished": current.get("isPublished", True),
        "locale": current.get("locale", settings.WIKIJS_DEFAULT_LOCALE),
        "path": safe_destination,
        "scriptCss": "",
        "scriptJs": "",
        "tags": [item["tag"] for item in current.get("tags", [])],
        "title": current["title"],
    }
    result = (await wiki.request(mutation, variables)).get("pages", {}).get(
        "update", {}
    )
    response_result = result.get("responseResult", {})
    if not response_result.get("succeeded"):
        raise RuntimeError(
            response_result.get("message", "Wiki.js page move failed.")
        )
    return result.get("page", {})


def destination_for_path(
    page_path: str, source_path: str, destination_path: str
) -> str:
    """Replace the source root of a page path with the destination root."""
    if page_path == source_path:
        return destination_path
    return destination_path + page_path[len(source_path):]


async def plan_page_tree_move(
    source_path: str,
    destination_path: str,
    locale: str,
) -> list[dict[str, Any]]:
    """Build and validate a page-tree move plan."""
    source = require_allowed_path(source_path)
    destination = require_allowed_path(destination_path)
    if source == destination:
        raise ValueError("Source and destination paths are the same.")
    if destination.startswith(source + "/"):
        raise ValueError("A page tree cannot be moved inside itself.")

    all_pages = await list_pages()
    locale_pages = [
        page for page in all_pages if page.get("locale") == locale
    ]
    moving = [
        page
        for page in locale_pages
        if page.get("path") == source
        or page.get("path", "").startswith(source + "/")
    ]
    if not moving:
        raise ValueError(f"No pages found at or below: {source}")

    moving_paths = {page["path"] for page in moving}
    existing_paths = {
        page["path"] for page in locale_pages if page["path"] not in moving_paths
    }
    plan: list[dict[str, Any]] = []
    destinations: set[str] = set()
    for page in moving:
        target = destination_for_path(page["path"], source, destination)
        require_allowed_path(target)
        if target in existing_paths:
            raise ValueError(f"Destination page already exists: {target}")
        if target in destinations:
            raise ValueError(f"Duplicate destination path: {target}")
        destinations.add(target)
        plan.append(
            {
                "id": page["id"],
                "sourcePath": page["path"],
                "destinationPath": target,
                "title": page["title"],
                "locale": locale,
                "updatedAt": page.get("updatedAt"),
            }
        )

    return sorted(
        plan,
        key=lambda item: item["sourcePath"].count("/"),
        reverse=True,
    )


@mcp.tool()
async def wikijs_move_page(
    source_path: str,
    destination_path: str,
    expected_updated_at: str,
    locale: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Preview or move one page. Set dry_run=false only after user confirmation."""
    page_locale = locale or settings.WIKIJS_DEFAULT_LOCALE
    source = require_allowed_path(source_path)
    destination = require_allowed_path(destination_path)
    page = await get_page_by_path(source, page_locale)
    if page.get("updatedAt") != expected_updated_at:
        raise RuntimeError(
            "The page changed after it was read. Read it again before moving."
        )

    pages = await list_pages()
    if any(
        item.get("locale") == page_locale
        and item.get("path") == destination
        and item.get("id") != page.get("id")
        for item in pages
    ):
        raise ValueError(f"Destination page already exists: {destination}")

    plan = {
        "id": page["id"],
        "sourcePath": source,
        "destinationPath": destination,
        "title": page["title"],
        "locale": page_locale,
        "updatedAt": page["updatedAt"],
    }
    if dry_run:
        return {"dryRun": True, "moves": [plan]}

    moved = await move_page_to_path(page, destination, expected_updated_at)
    return {"dryRun": False, "moved": [moved], "failed": []}


@mcp.tool()
async def wikijs_move_page_tree(
    source_path: str,
    destination_path: str,
    locale: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Preview or move a page and all descendants. Set dry_run=false after confirmation."""
    page_locale = locale or settings.WIKIJS_DEFAULT_LOCALE
    plan = await plan_page_tree_move(source_path, destination_path, page_locale)
    if dry_run:
        return {"dryRun": True, "moves": plan}

    moved: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for item in plan:
        try:
            page = await get_page_by_path(item["sourcePath"], page_locale)
            result = await move_page_to_path(
                page,
                item["destinationPath"],
                item["updatedAt"],
            )
            moved.append(result)
        except Exception as exc:
            logger.exception(
                "Failed to move Wiki.js page %s to %s",
                item["sourcePath"],
                item["destinationPath"],
            )
            failed.append(
                {
                    "sourcePath": item["sourcePath"],
                    "destinationPath": item["destinationPath"],
                    "error": str(exc),
                }
            )
            break

    return {
        "dryRun": False,
        "moved": moved,
        "failed": failed,
        "remaining": plan[len(moved) + len(failed):],
        "succeeded": not failed,
    }


if __name__ == "__main__":
    logger.info(
        "Starting V4X Wiki.js MCP on %s:%s for path %s",
        settings.MCP_HOST,
        settings.MCP_PORT,
        allowed_prefixes(),
    )
    mcp.run(transport="http", host=settings.MCP_HOST, port=settings.MCP_PORT)
