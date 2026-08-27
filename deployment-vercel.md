# Deployment Guide — Vercel

Scope: deploying [`app.py`](app.py) — the whole backend, a Flask app — to Vercel. Document
Intelligence and Storage stay on Azure as *services*; nothing here runs on Azure compute.

This doc reflects Vercel's Python runtime docs as of 2026-08-24 (linked throughout) — Vercel
changes these conventions periodically, so re-check against current docs if something here stops
matching what you see.

---

## 1. What Vercel's platform dictates about this code (read this first)

Three platform facts shaped the code itself, not just its config:

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
   Real multi-page ballooned drawings routinely exceed that, which is why `app.py` has a second,
   blob-first upload path (§4 below) that routes file bytes around the function entirely.

3. **Duration and bundle size are non-issues here.**
   - Max function duration: **300s on both Hobby and Pro by default** (Pro can raise it to 800s,
     or 1800s in beta) — comfortably covers this workload's typical 30s–2min per drawing.
     `vercel.json` sets `maxDuration: 300` explicitly.
     [[Functions limits, Max duration](https://vercel.com/docs/functions/limitations#max-duration)]
   - Python function bundle size: **500MB uncompressed** (vs. 250MB for other runtimes), which
     comfortably fits PyMuPDF + Pillow + two Azure SDKs + the Anthropic SDK.
     [[Functions limits, Bundle size](https://vercel.com/docs/functions/limitations#bundle-size-limits)]

   `vercel.json` still excludes `tests/`, `doc/`, and other dev-only content from the bundle as
   good practice, not because 500MB was actually tight.

---

## 2. Prerequisites

| Requirement | Notes |
|---|---|
| An Azure Storage account | Blob (drawing files + Excel exports) and Table (job records). Containers and tables are created on first use by the code. |
| An Azure AI Document Intelligence resource | Layout/OCR. Copy its endpoint + key. |
| An Anthropic API key | console.anthropic.com — for the structured extraction call. |
| A Vercel account + the Vercel CLI (≥48.2.10) | `npm install -g vercel`, then `vercel login`. Only needed to deploy — local development doesn't require it, see §5. |
| Python 3.12 | `.python-version` pins it, matching Vercel's supported range. |

---

## 3. Auth — read this, it's a real gap

There is no platform-level auth gate in front of a Vercel Function. `app.py` fills that gap itself
with a shared-secret header (`x-api-key`, checked against `API_ACCESS_KEY`), enforced on every
route except `/api/health`. This is **not real auth** (see `doc/architecture-poc.md` §3.3) — it's a
shared secret, not per-user identity. Two things to get right:

- `API_ACCESS_KEY` is checked eagerly on every request, not just validated at cold start — if it's
  unset, every request 500s with a clear `ConfigurationError` rather than the endpoint silently
  accepting all traffic. Don't skip setting it in production "to test quickly."
- CORS defaults to `Access-Control-Allow-Origin: *` (`CORS_ALLOWED_ORIGIN` env var, see
  `.env.example`) — convenient for local dev and curl, but restrict it to your actual frontend's
  origin before sharing a live URL with anyone else.

---

## 4. The two upload paths

| | `POST /api/drawings/extract` | `POST /api/drawings/upload-url` → `POST /api/drawings/<jobId>/process` |
|---|---|---|
| Use for | Quick tests, small files well under 4.5MB | Anything larger — in practice, most real multi-page drawings |
| How it works | One multipart POST, file bytes go through this function | Client PUTs the file directly to Blob Storage via a SAS URL this function hands out; this function never sees the bytes |

[`wabtec_poc_app`](../wabtec_poc_app) implements both and picks between them automatically by file
size (`extractDrawingSmart` in its `src/lib/api.ts`), so from the browser this is invisible. The
curl equivalents:

**Small-file path:**

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

---

## 5. Local development

No Vercel CLI needed — it's a plain Flask app:

```bash
python -m venv .venv
.venv\Scripts\activate           # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in AZURE_STORAGE_CONNECTION_STRING, DOCUMENT_INTELLIGENCE_*, CLAUDE_API_KEY, and generate
# an API_ACCESS_KEY: openssl rand -hex 32
python app.py                    # http://127.0.0.1:8000
```

`app.py` loads `.env` itself via python-dotenv. Smoke test:

```bash
curl "http://127.0.0.1:8000/api/health"     # {"status":"ok"} -- unauthenticated
curl -X POST "http://127.0.0.1:8000/api/drawings/extract" \
  -H "x-api-key: <your API_ACCESS_KEY>" -F "file=@/path/to/ballooned-drawing.pdf"
```

Two things a local run does **not** fake: Document Intelligence and Claude are called for real
(local means the compute, not the services — expect real API spend per extraction), and Vercel's
4.5MB body cap doesn't apply, so a large file that works locally can still fail once deployed
unless the client uses the §4 large-file path. The frontend already routes by size, so this only
bites hand-rolled curl testing.

If you do have the CLI and want the deployed routing/env behaviour instead, `vercel dev` works too
and serves on port 3000.

---

## 6. Deploy

```bash
vercel env add AZURE_STORAGE_CONNECTION_STRING production
vercel env add DOCUMENT_INTELLIGENCE_ENDPOINT production
vercel env add DOCUMENT_INTELLIGENCE_KEY production
vercel env add CLAUDE_API_KEY production
vercel env add CLAUDE_MODEL production            # e.g. claude-sonnet-5
vercel env add API_ACCESS_KEY production
vercel env add CORS_ALLOWED_ORIGIN production     # your frontend's origin, not *

vercel --prod
```

`vercel env add` prompts you to paste each value — nothing sensitive touches your shell history or
a committed file. Repeat with `preview` instead of `production` if you also want preview
deployments to work (they get a different env scope by default).

Deploy the backend first: the frontend needs this deployment's URL and `API_ACCESS_KEY` entered in
its connection form, and this deployment needs the frontend's origin in `CORS_ALLOWED_ORIGIN` — so
once the frontend is up, come back, `vercel env add CORS_ALLOWED_ORIGIN`, and redeploy.

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

or the Vercel dashboard's Functions/Logs tab. `logger.exception(...)` runs before every 5xx
response, so look for `ExtractionServiceError`, `Claude extraction call failed`, and
`Extraction gave up after` in the log stream.

---

## 9. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Every request returns `500 ConfigurationError` mentioning `API_ACCESS_KEY` | You deployed without running `vercel env add API_ACCESS_KEY` — see §3. |
| Every request returns `500 ConfigurationError` naming some other variable | That env var isn't set in this deployment's scope — note `preview` and `production` are separate scopes (§6). |
| Every request returns `401 Unauthorized` | `x-api-key` header missing or doesn't match `API_ACCESS_KEY` exactly — check for trailing whitespace if you pasted it. |
| `413` on `POST /api/drawings/extract` with no JSON error body from this app | Hit Vercel's 4.5MB platform body cap before your code ran — use the three-step upload-url/process flow instead (§4), not a bug to fix in application code. |
| `404` calling `/api/drawings/extract`, app otherwise seems deployed | Vercel picked file-based `/api` routing instead of the Flask framework preset, or didn't find `app.py` at a recognized entrypoint location — confirm `flask` is actually listed in `requirements.txt` and `app.py` is at the project root, not nested under `api/`. |
| Deploy fails during build with a size-related error | Should be rare given the 500MB Python limit (§1) — check `vercel.json`'s `excludeFiles` glob is actually matching, and that nothing unexpectedly large snuck into the repo root. |
| CORS error in the browser console calling this from `wabtec_poc_app` | `CORS_ALLOWED_ORIGIN` doesn't match the frontend's actual origin exactly (scheme + host, no trailing slash) — or it's pinned to one origin and you're testing from a Vercel *preview* URL, which has a different hostname. |
| Function times out at 300s | One drawing exceeded the ceiling — lower `MAX_PAGES`, or accept it as the known POC limitation it is; there's no queue to fall back on (see the README's limitations list). |
| `ImportError` for anything under `src.*` at cold start | Vercel needs to trace `app.py`'s imports back to the project-root `src/` package at build time — if it fails, check the project's Root Directory setting points at the repo root, not a subfolder. |
