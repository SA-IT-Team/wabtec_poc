# Ballooned Drawing Extraction — POC Backend

Implements [`doc/architecture-poc.md`](doc/architecture-poc.md): validates whether Azure AI
Document Intelligence + Claude can reliably read balloon numbers, dimensions, tolerances, and
GD&T off real ballooned drawings. No auth beyond a shared secret, no reconciliation gate — see
that document's §5 for exactly what this build does and does not prove.

**Plain Python, one deployment target: Vercel.** [`app.py`](app.py) is a Flask app; everything it
needs is in [`src/`](src). Azure is used for two *services* — Document Intelligence (layout/OCR)
and Storage (Blob for files, Table for job records) — but nothing here runs on Azure compute, and
there is no Azure Functions code left in the repo. The React frontend lives in a separate repo,
[`wabtec_poc_app`](../wabtec_poc_app), and also deploys to Vercel.

> **Provider note:** the original design used Azure OpenAI for the structured extraction call;
> this build uses **Claude (Anthropic Messages API)** instead — Document Intelligence remains on
> Azure for layout/OCR. `doc/architecture-poc.md` still describes the Azure OpenAI version, and
> also still describes an Azure Functions host, and has not been updated to match; treat this
> README, [`deployment-vercel.md`](deployment-vercel.md), and the code itself as current.

## API

| | |
|---|---|
| `POST /api/drawings/extract` | Upload + synchronously process one drawing (multipart). Files under ~4MB only — see below. |
| `POST /api/drawings/upload-url` | Step 1 of the large-file path: returns a direct-to-Blob-Storage SAS URL. |
| `POST /api/drawings/<jobId>/process` | Step 2: process a drawing already uploaded via that SAS. |
| `GET /api/drawings/<jobId>` | Re-fetch a previously computed result. |
| `GET /api/health` | Liveness check — the only unauthenticated route. |

Every route except `/api/health` requires an `x-api-key` header matching `API_ACCESS_KEY`.

Two upload paths exist because Vercel caps request bodies at 4.5MB platform-wide and real
multi-page drawings routinely exceed that — see [`deployment-vercel.md`](deployment-vercel.md) §4.
The frontend picks between them automatically by file size.

## Project layout

```
wabtec_poc/
  app.py                   # Flask app: routing, auth, CORS, error mapping -- the only entry point
  src/
    config.py               # env-var settings
    exceptions.py            # domain error types -> HTTP status mapping
    models.py                 # pydantic models (also double as the Claude tool input_schema)
    preprocessor.py            # PDF/image -> page raster images, DPI/page-count validation
    balloon_detector.py         # Document Intelligence layout call -> balloon candidates
    extraction_orchestrator.py   # Claude structured extraction (forced tool use) + repair loop
    tolerance_normalizer.py       # normalizes tolerance notation
    excel_writer.py                # template-driven .xlsx generation
    job_store.py                    # Table Storage (+ in-memory) job records
    upload_handler.py                # upload validation + source-blob write
    storage_helpers.py                # Blob Storage helpers (containers, SAS, reads)
    pipeline_factory.py                # wires the pipeline from Settings
    ai_clients.py                       # Adapter pattern over Document Intelligence + Claude (+ Fake* test doubles)
    pipeline.py                          # Pipeline pattern wiring every stage together
    retry.py                              # shared retry/backoff policy
  tests/
    unit/            # one file per module, no real Azure/Claude credentials touched
    integration/      # full pipeline wired with Fake* clients, no network calls
    fixtures/           # canned Document Intelligence layout + extraction JSON
  vercel.json      # function duration + bundle excludes
  .env.example      # every env var this app reads
```

## Local setup

```bash
python -m venv .venv
.venv\Scripts\activate           # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Running the tests

```bash
pytest -q                                      # 51 tests, no Azure/Claude credentials required
pytest -q --cov=src --cov-report=term-missing  # coverage report
```

Every unit and integration test runs against `Fake*` implementations of the AI clients
(`src/ai_clients.py`) — no network calls, no credentials needed. `tests/unit/test_app.py` exercises
the Flask app the same way, with `build_pipeline`/storage helpers monkeypatched. The only untested
code paths are the *real* SDK-backed classes (`AzureDocumentIntelligenceClient`, `ClaudeChatClient`,
`TableStorageJobStore`) and `config.py`'s env-var loading, which need live Azure/Anthropic
credentials — exercise those with a real local run (below) or a manual smoke test.

## Running it locally

```bash
cp .env.example .env
# fill in AZURE_STORAGE_CONNECTION_STRING, DOCUMENT_INTELLIGENCE_*, CLAUDE_API_KEY, API_ACCESS_KEY
python app.py                    # http://127.0.0.1:8000
```

No Vercel CLI, no emulators, no Docker — `app.py` loads `.env` itself and Flask serves it. (If you
do have the Vercel CLI and want the deployed routing/env behaviour instead, `vercel dev` also works
and serves on port 3000.)

Smoke test it:

```bash
curl "http://127.0.0.1:8000/api/health"          # {"status":"ok"} -- no key needed

curl -X POST "http://127.0.0.1:8000/api/drawings/extract" \
  -H "x-api-key: <your API_ACCESS_KEY>" \
  -F "file=@/path/to/ballooned-drawing.pdf"

curl "http://127.0.0.1:8000/api/drawings/<jobId>" -H "x-api-key: <your API_ACCESS_KEY>"
```

Then point the frontend at it: run `npm run dev` in `../wabtec_poc_app` and enter
`http://127.0.0.1:8000` plus your `API_ACCESS_KEY` in its connection form. CORS defaults to `*`,
so no extra config is needed for local work.

Note that a *local* run still calls the real Azure Document Intelligence and Claude APIs — "local"
here means the compute, not the services. You need real credentials and each extraction costs real
API spend.

## Deploying

See [`deployment-vercel.md`](deployment-vercel.md) — env vars via `vercel env add`, then
`vercel --prod`.

## Known POC limitations (by design — see `doc/architecture-poc.md` §1.4, §5)

- Balloon detection is a regex heuristic over Document Intelligence's OCR tokens, not a trained
  shape-detection model — expect false positives/negatives on dense or hand-annotated drawings.
- No auth beyond a shared secret (`x-api-key`); no multi-user concurrency; no reconciliation/
  sign-off gate.
- `TableStorageJobStore` uses a single fixed partition key — fine at POC volume only.
- Processing is synchronous inside one request: a drawing that takes longer than the function's
  300s ceiling has no queue or retry to fall back on.
- 30-day blob retention is a safety default for throwaway infrastructure, not a compliance policy.
