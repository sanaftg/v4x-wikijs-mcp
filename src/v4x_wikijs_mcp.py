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


@mcp.tool()
async def wikijs_list_pages() -> list[dict[str, Any]]:
    """List page metadata under the configured V4X path only."""
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
async def wikijs_search_pages(query: str) -> list[dict[str, Any]]:
    """Search V4X page titles, paths, descriptions, and content."""
    term = query.strip().casefold()
    if not term:
        return []

    matches: list[dict[str, Any]] = []
    for metadata in await wikijs_list_pages():
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


if __name__ == "__main__":
    logger.info(
        "Starting V4X Wiki.js MCP on %s:%s for path %s",
        settings.MCP_HOST,
        settings.MCP_PORT,
        allowed_prefixes(),
    )
    mcp.run(transport="http", host=settings.MCP_HOST, port=settings.MCP_PORT)
