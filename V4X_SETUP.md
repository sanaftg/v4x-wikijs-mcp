# V4X ChatGPT setup

This branch adds a restricted, remote-capable MCP server for the existing V4X Wiki.js instance.

## Safety model

- Only pages at `WIKIJS_ALLOWED_PATH_PREFIX` or below it are exposed.
- No delete or bulk mutation tools are registered.
- Updates require the page's current `updatedAt` value to prevent stale overwrites.
- The Wiki.js API key remains on the VPS.
- The Docker service publishes only to `127.0.0.1`; do not expose port 8000 directly.

## Tools

- `wikijs_connection_status`
- `wikijs_list_pages`
- `wikijs_search_pages`
- `wikijs_get_page`
- `wikijs_create_page`
- `wikijs_update_page`

## VPS setup

Do not run the upstream `docker.yml`; it creates another Wiki.js installation.

```bash
cp config/v4x.env.example .env.v4x
```

Edit `.env.v4x` on the VPS and set:

- `WIKIJS_API_URL`: URL reachable from the MCP container.
- `WIKIJS_API_KEY`: API key created in Wiki.js Administration > API Access.
- `WIKIJS_ALLOWED_PATH_PREFIX`: the actual Wiki.js path containing V4X pages.
- `WIKIJS_DEFAULT_LOCALE`: the locale code used by those pages.

Start the MCP service:

```bash
docker compose -f compose.v4x.yml up -d --build
docker compose -f compose.v4x.yml logs -f v4x-wikijs-mcp
```

The Streamable HTTP endpoint is available locally at:

```text
http://127.0.0.1:8000/mcp
```

Verify it with MCP Inspector before connecting ChatGPT:

```bash
npx @modelcontextprotocol/inspector@latest
```

## Connecting ChatGPT

Keep the service bound to localhost and connect it through OpenAI Secure MCP Tunnel. Do not proxy the unauthenticated endpoint directly to the public internet.

After the tunnel is available:

1. Enable Developer mode in ChatGPT.
2. Open ChatGPT Plugins and select the plus button.
3. Choose Tunnel and enter/select the tunnel ID.
4. Review the six advertised tools.
5. Test read-only prompts before trying a create or update.

## Initial checks

1. Call `wikijs_connection_status`.
2. Call `wikijs_list_pages` and verify that no paths outside V4X are returned.
3. Read one known page.
4. Create a disposable page below the allowed prefix.
5. Update it using its returned/current `updatedAt`.
6. Delete the disposable page manually in Wiki.js.
