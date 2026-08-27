# Deployment Guide — Vercel

Scope: deploying [`app.py`](app.py) (a Flask app implementing the same endpoints as
`function_app.py`) to Vercel instead of Azure Functions. Storage (Blob + Table) and Document
Intelligence stay on Azure regardless — only the **compute host** and the **LLM provider** moved.
See [`deployment.md`](deployment.md) for the Azure Functions path this replaces.

This doc reflects Vercel's Python runtime docs as of 2026-08-24 (linked throughout) — Vercel
changes these conventions periodically, so re-check against current docs if something here stops
matching what you see.

---

## 1. What's actually different about this port (read this first)

Three things about Vercel's platform genuinely shaped this code, not just its config:

1. **Entrypoint location.** Vercel detects a Python web framework by finding `flask` (or
   `fastapi`, `django`, …) in `requirements.txt`, then looks for an `app` object in `app.py`,
   `index.py`, `server.py`, `main.py`, `wsgi.py`, or `asgi.py` — **at the project root, or inside
   `src/`/`app/`.** [[Python runtime reference](https://vercel.com/docs/functions/runtimes/python)]
   That's why the Flask app lives at [`app.py`](app.py) in the repo root, not under an `/api/`
   directory (that's a *different*, older, file-based-routing convention that a detected framework
   preset overrides anyway). Vercel routes every request straight to this one app — the
   `@app.route(...)` decorators inside it **are** the routing; no `vercel.json` rewrites needed.

2. **Request/response body size is capped at 4.5MB, platform-wide, not configurable.**
   [[Vercel Functions limits](https://vercel.com/docs/functions/limitations#request-body-size)]
   Real multi-page ballooned drawings routinely exceed that. This is the one genuine architecture
   change in this port: `app.py` adds a second, blob-first upload path (§4 below) specifically to
   route file bytes around this function entirely. `function_app.py` (Azure) has no such cap and
   keeps its single-call multipart upload unchanged.

3. **Duration and bundle size turned out to be a non-issue** — flagged here because an earlier
   draft of this doc warned about both before I'd verified the actual numbers. Corrected:
   - Max function duration: **300s on both Hobby and Pro by default** (Pro can raise it to 800s,
     or 1800s in beta) — comfortably covers this workload's typical 30s–2min per drawing.
     [[Functions limits, Max duration](https://vercel.com/docs/functions/limitations#max-duration)]
   - Python function bundle size: **500MB uncompressed** (vs. 250MB for other runtimes), which
     comfortably fits PyMuPDF + Pillow + three Azure SDKs + the Anthropic SDK.
     [[Functions limits, Bundle size](https://vercel.com/docs/functions/limitations#bundle-size-limits)]
   `vercel.json` still excludes `tests/`, `doc/`, and other dev-only content from the bundle as
   good practice, not because 500MB was actually tight.

---

## 2. Prerequisites

Same Azure resources as the Azure Functions path (Document Intelligence, Storage account) plus:

| Requirement | Notes |
|---|---|
| Azure Storage account + Document Intelligence resource | Provision exactly as in [`deployment.md`](deployment.md) §2.1 — skip the Function App / Premium plan steps, none of that applies here. |
| An Anthropic API key | Same as `deployment.md` §1 — console.anthropic.com. |
| A Vercel account + the Vercel CLI (≥48.2.10) | `npm install -g vercel`, then `vercel login`. |
| **If you also use `pyenv` locally:** know that `.python-version` (pinned to `3.12` here, for Vercel's supported range — see §1) will make `pyenv` switch this whole repo to 3.12, including when you're working on the Azure Functions side, which targets 3.10/3.11. Either switch manually per task, or keep two separate local venvs (you're already doing this: `.venv/` for Azure work vs. whatever `vercel dev` provisions). |

---

## 3. Auth — read this, it's a real gap Azure's version didn't have

Azure Functions' `AuthLevel.FUNCTION` gates every request at the *host* level, before your code
runs — Vercel has no equivalent. `app.py` fills that gap itself with a shared-secret header
(`x-api-key`, checked against `API_ACCESS_KEY`), enforced on every route except `/api/health`. This
is **not real auth**, same caveat as the Azure version (see `doc/architecture-poc.md` §3.3) — it's
a shared secret, not per-user identity. Two things to get right:

- `API_ACCESS_KEY` is checked eagerly on every request, not just validated at cold start — if it's
  unset, every request 500s with a clear `ConfigurationError` rather than the endpoint silently
  accepting all traffic. Don't skip setting it in production "to test quickly."
- CORS defaults to `Access-Control-Allow-Origin: *` (`CORS_ALLOWED_ORIGIN` env var, see
  `.env.example`) — fine while only you are hitting this with curl, but restrict it to your actual
  frontend's origin before sharing a live URL with anyone else.

---

## 4. The two upload paths

| | `POST /api/drawings/extract` | `POST /api/drawings/upload-url` → `POST /api/drawings/<jobId>/process` |
|---|---|---|
| Use for | Quick tests, small files well under 4.5MB | Anything larger — in practice, most real multi-page drawings |
| How it works | One multipart POST, file bytes go through this function | Client PUTs the file directly to Blob Storage via a SAS URL this function hands out; this function never sees the bytes |

**Small-file path** (unchanged in spirit from the Azure version):

```bash
curl -X POST "${BASE_URL}/api/drawings/extract" \
  -H "x-api-key: <your API_ACCESS_KEY>" \
  -F "file=@/path/to/small-drawing.pdf"
```

**Large-file path** — three requests instead of one:

```bash
# 1. Ask for an upload URL
RESP=$(curl -s -X POST "${BASE_URL}/api/drawings/upload-url" \
  -H "x-api-key: <your API_ACCESS_KEY>" -H "Content-Type: application/json" \
  -d '{"fileName": "big-drawing.pdf", "contentType": "application/pdf"}')
JOB_ID=$(echo "$RESP" | jq -r .jobId)
UPLOAD_URL=$(echo "$RESP" | jq -r .uploadUrl)
BLOB_PATH=$(echo "$RESP" | jq -r .blobPath)

# 2. PUT the file directly to Blob Storage (bypasses this app's 4.5MB cap entirely)
curl -X PUT "$UPLOAD_URL" \
  -H "x-ms-blob-type: BlockBlob" -H "Content-Type: application/pdf" \
  --data-binary "@/path/to/big-drawing.pdf"

# 3. Trigger processing of what's now sitting in Blob Storage
curl -X POST "${BASE_URL}/api/drawings/${JOB_ID}/process" \
  -H "x-api-key: <your API_ACCESS_KEY>" -H "Content-Type: application/json" \
  -d "{\"blobPath\": \"${BLOB_PATH}\", \"contentType\": \"application/pdf\"}"
```

Step 3's response is the same `ExtractionResult` JSON shape as the small-file path.

> **Frontend follow-up, flagged not fixed:** [`wabtec_poc_app`](../wabtec_poc_app) currently only
> implements the single-call multipart flow (matching `function_app.py`). If you're pointing that
> React app at a Vercel-hosted backend, it needs the three-step flow above added for files over
> ~4MB, or uploads of real drawings will fail with a platform-level `413` before your code even
> runs. That's a separate, scoped change to the frontend — say the word if you want it done.

---

## 5. Local development

```bash
cp .env.example .env
# fill in AZURE_STORAGE_CONNECTION_STRING, DOCUMENT_INTELLIGENCE_*, CLAUDE_API_KEY, and generate
# an API_ACCESS_KEY: openssl rand -hex 32
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
vercel dev
```

`vercel dev` reads `.env` automatically. Smoke test:

```bash
curl "http://localhost:3000/api/health"     # {"status": "ok"} -- unauthenticated
curl -X POST "http://localhost:3000/api/drawings/extract" \
  -H "x-api-key: <your API_ACCESS_KEY>" -F "file=@/path/to/ballooned-drawing.pdf"
```

---

## 6. Deploy

```bash
vercel env add AZURE_STORAGE_CONNECTION_STRING production
vercel env add DOCUMENT_INTELLIGENCE_ENDPOINT production
vercel env add DOCUMENT_INTELLIGENCE_KEY production
vercel env add CLAUDE_API_KEY production
vercel env add CLAUDE_MODEL production          # e.g. claude-sonnet-5
vercel env add API_ACCESS_KEY production
vercel env add CORS_ALLOWED_ORIGIN production     # your frontend's origin, not *

vercel --prod
```

`vercel env add` prompts you to paste each value — nothing sensitive touches your shell history or
a committed file. Repeat with `preview` instead of `production` if you also want preview
deployments to work (they get a different env scope by default).

---

## 7. Smoke test

```bash
BASE_URL="https://<your-project>.vercel.app"
curl "${BASE_URL}/api/health"
curl -X POST "${BASE_URL}/api/drawings/extract" \
  -H "x-api-key: <your API_ACCESS_KEY>" -F "file=@/path/to/small-ballooned-drawing.pdf"
```

For anything approaching or over 4.5MB, use the three-step flow in §4 instead — a large file
posted to `/extract` directly will fail with a platform-level `413` before your application code
runs at all (you won't see your own `PageLimitExceeded`/`QualityThresholdNotMet` errors for this —
those only fire once a request actually reaches the app).

---

## 8. Monitoring

```bash
vercel logs <deployment-url-or-id>
```

or the Vercel dashboard's Functions/Logs tab. Application-level errors are logged the same way as
the Azure version (`logger.exception(...)` before every 5xx response) — look for the same
`ExtractionServiceError` / `Claude extraction call failed` / `Extraction gave up after` messages
documented in `deployment.md` §5 and §8; the failure modes are identical, only where you go to read
the log differs.

---

## 9. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Every request returns `500 ConfigurationError` mentioning `API_ACCESS_KEY` | You deployed without running `vercel env add API_ACCESS_KEY` — see §3. |
| Every request returns `401 Unauthorized` | `x-api-key` header missing or doesn't match `API_ACCESS_KEY` exactly — check for trailing whitespace if you pasted it. |
| `413` on `POST /api/drawings/extract` with no JSON error body from this app | Hit Vercel's 4.5MB platform body cap before your code ran — use the three-step upload-url/process flow instead (§4), not a bug to fix in application code. |
| `404` calling `/api/drawings/extract` or similar, app otherwise seems deployed | Vercel picked file-based `/api` routing instead of the Flask framework preset, or didn't find `app.py` at a recognized entrypoint location — confirm `flask` is actually listed in `requirements.txt` and `app.py` is at the project root, not nested under `api/`. |
| Deploy fails during build with a size-related error | Should be rare given the 500MB Python limit (§1) — check `vercel.json`'s `excludeFiles` glob is actually matching, and that nothing unexpectedly large snuck into the repo root. |
| CORS error in the browser console calling this from `wabtec_poc_app` | `CORS_ALLOWED_ORIGIN` doesn't match the frontend's actual origin exactly (scheme + host, no trailing slash) — or it's still unset and you're expecting a specific origin instead of the `*` default. |
| `ImportError` for anything under `src.*` at cold start | Vercel needs to trace `app.py`'s imports back to the project-root `src/` package at build time — this is the part of this port I could least verify without a real deploy; if it fails, it's almost certainly a Vercel project-configuration issue (root directory setting), not an application bug. |
