# Ballooned Drawing Extraction — POC

Implements [`doc/architecture-poc.md`](doc/architecture-poc.md): a single Azure Function App that
validates whether Azure AI Document Intelligence + Claude can reliably read balloon numbers,
dimensions, tolerances, and GD&T off real ballooned drawings. No auth, no reconciliation gate —
see that document's §5 for exactly what this build does and does not prove.

> **Provider note:** the original design used Azure OpenAI for the structured extraction call;
> this build uses **Claude (Anthropic Messages API)** instead — Document Intelligence remains on
> Azure for layout/OCR. `doc/architecture-poc.md` still describes the Azure OpenAI version and has
> not been updated to match; treat this README, `deployment.md`, and the code itself as current.

## Project layout

```
wabtec_poc/
  function_app.py          # Azure Functions v2 HTTP triggers (the only entry point)
  src/
    config.py               # env-var settings
    exceptions.py            # domain error types -> HTTP status mapping
    models.py                 # pydantic models (also double as the Claude tool input_schema)
    preprocessor.py            # PDF/image -> page raster images, DPI/page-count validation
    balloon_detector.py         # Document Intelligence layout call -> balloon candidates
    extraction_orchestrator.py  # Claude structured extraction (forced tool use) + repair loop
    tolerance_normalizer.py     # normalizes tolerance notation
    excel_writer.py              # template-driven .xlsx generation
    job_store.py                  # Table Storage (+ in-memory) job records
    upload_handler.py              # upload validation + blob storage
    ai_clients.py                   # Adapter pattern over Document Intelligence + Claude (+ Fake* test doubles)
    pipeline.py                      # Pipeline pattern wiring every stage together
    retry.py                          # shared retry/backoff policy
  tests/
    unit/            # one file per module, no real Azure/Claude credentials touched
    integration/      # full pipeline wired with Fake* clients, no network calls
    fixtures/           # canned Document Intelligence layout + extraction JSON
```

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
```

## Running the tests

```bash
pytest -q                                    # 34 tests, no Azure credentials required
pytest -q --cov=src --cov-report=term-missing  # coverage report
```

Every unit and integration test runs against `Fake*` implementations of the AI clients
(`src/ai_clients.py`) — no network calls, no credentials needed. The only untested code paths
are the *real* SDK-backed classes (`AzureDocumentIntelligenceClient`, `ClaudeChatClient`,
`TableStorageJobStore`) and `config.py`'s env-var loading, which need live Azure/Anthropic
credentials — exercise those via a real `func start` run (below) or a manual smoke test.

## Running the Function App locally

Requires [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local)
and [Azurite](https://learn.microsoft.com/azure/storage/common/storage-use-azurite) (or a real
Storage account) for `AzureWebJobsStorage`.

```bash
cp local.settings.json.example local.settings.json
# fill in DOCUMENT_INTELLIGENCE_* with your Azure resource values, and CLAUDE_API_KEY with a
# key from console.anthropic.com
azurite &                # if using local storage emulation
func start
```

Then, from another terminal:

```bash
curl -X POST "http://localhost:7071/api/drawings/extract" \
  -H "x-functions-key: <local dev key, or omit -- AuthLevel.FUNCTION is skipped by `func start`>" \
  -F "file=@/path/to/ballooned-drawing.pdf"
```

```bash
curl "http://localhost:7071/api/drawings/<jobId>"
```

## Known POC limitations (by design — see architecture-poc.md §1.4, §5)

- Balloon detection is a regex heuristic over Document Intelligence's OCR tokens, not a trained
  shape-detection model — expect false positives/negatives on dense or hand-annotated drawings.
- No auth beyond the Function key; no multi-user concurrency; no reconciliation/sign-off gate.
- `TableStorageJobStore` uses a single fixed partition key — fine at POC volume only.
- 30-day blob retention is a safety default for throwaway infrastructure, not a compliance policy.
