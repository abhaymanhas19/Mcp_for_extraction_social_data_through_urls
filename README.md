# Scrape Social Media by Evidano — Python server

A Python port of the TypeScript MCP server in [`../src`](../src). It exposes the same `scrape_social_media` tool, actions, input schema, responses, and HTTP routes. The TypeScript server is unchanged and is still the production deployment.

## Layout

| Python module | TypeScript source |
|---|---|
| `config.py` | `config.ts` |
| `errors.py`, `types.py` | `errors.ts`, `types.ts` |
| `validation.py` | `validation.ts` |
| `platforms.py` | `platforms.ts` |
| `upstream.py` | `upstream.ts` |
| `cleaning.py`, `normalize.py`, `csv_export.py` | `cleaning.ts`, `normalize.ts`, `csv.ts` |
| `metadata.py` | `metadata.ts` |
| `service.py` | `service.ts` |
| `mcp_server.py` | `mcp.ts` |
| `http_app.py`, `__main__.py` | `http.ts`, `index.ts` |
| `js.py` | — (JavaScript-compatible URL parsing, number formatting, and JSON so outputs match the TypeScript server) |

## Run locally

Requires Python 3.11 or later (tested on 3.13).

```bash
cd python
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export UPSTREAM_API_KEY=...            # from your secret manager; never commit it
export PUBLIC_BASE_URL=http://localhost:3000
.venv/bin/python -m evidano_social_scraper
```

The server uses the same environment variables as the TypeScript server: `UPSTREAM_API_KEY` (or `KEY`), `UPSTREAM_API_BASE_URL`, `PUBLIC_BASE_URL`, `ALLOWED_HOSTS`, `PORT`, `UPSTREAM_TIMEOUT_MS`, `WAIT_POLL_MS`, and `OPENAI_APPS_CHALLENGE`. The `Host` header must match `ALLOWED_HOSTS`. If that is unset, it must match the host of `PUBLIC_BASE_URL`.

Routes: `POST /mcp` (stateless Streamable HTTP with JSON responses), `GET /healthz`, `GET /.well-known/openai-apps-challenge`, and `GET|HEAD /exports/:jobId.json|csv`.

## Docker

```bash
docker build -t evidano-social-scraper-py python/
docker run -p 3000:3000 -e UPSTREAM_API_KEY -e PUBLIC_BASE_URL=http://localhost:3000 evidano-social-scraper-py
```

## Connect a client

```bash
claude mcp add --transport http evidano-social-scraper-py http://localhost:3000/mcp
```

## Known differences from the TypeScript server

- Invalid tool arguments return an `isError` result with the text `Input validation error: ...`. The wording of each message is close to zod's, but not identical.
- Processor JSON integers above 2^53 keep their exact value. In TypeScript they are rounded.
- There is no Vercel function entry point. Run the server as a process or a container.

## Keeping in sync

Platform tables, URL rules, and guidance text are copied from the TypeScript source. A change to `src/` needs the same change here.
