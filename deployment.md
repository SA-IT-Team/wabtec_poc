# Deployment Guide — Ballooned Drawing Extraction POC

Scope: deploying the [`poc/`](.) Azure Function App to a real Azure subscription so it can be
smoke-tested against actual ballooned drawings. This is deliberately **not** a production
deployment — see [`architecture-poc.md`](../architecture-poc.md) §1.4/§5 and
[`README.md`](README.md) for what this build intentionally omits (auth beyond a function key,
reconciliation gate, multi-user concurrency). Treat everything provisioned here as **disposable**:
tear it down when the extraction-accuracy question is answered (§4 below).

---

## 1. Prerequisites

| Requirement | Notes |
|---|---|
| Azure subscription | With permission to create resource groups and Cognitive Services accounts. |
| **Azure OpenAI access enabled** | Azure OpenAI still gates access on some subscriptions — confirm you can create an `OpenAI` kind Cognitive Services resource *before* running the steps below, not during. |
| Azure CLI (`az`) | v2.60+. `az login` and `az account set --subscription <id>` first. |
| Azure Functions Core Tools v4 | `func --version` → 4.x. Needed for local build/publish of the Python app. |
| Python 3.10 or 3.11 | Must match a version currently supported by the Azure Functions Python worker — check [the current support table](https://learn.microsoft.com/azure/azure-functions/functions-reference-python) before picking a runtime version, as supported versions change over time. |
| A region that has **both** Document Intelligence and Azure OpenAI (with a `gpt-4o`-class vision + structured-outputs-capable model) available | `eastus`, `eastus2`, and `westeurope` are safe bets as of this writing — re-verify against the [Azure OpenAI model availability table](https://learn.microsoft.com/azure/ai-services/openai/concepts/models) since it changes frequently. |

---

## 2. Provision Azure resources

Two equivalent paths. Use the CLI walkthrough for a first deploy so you understand every piece;
use the Bicep template (Appendix A) once you're repeating the setup.

### 2.1 Quickest path — Azure CLI

```bash
# ---- variables -------------------------------------------------------------
SUFFIX="poc01"                        # change to something unique to you
LOCATION="eastus"
RG="bdx-${SUFFIX}-rg"
STORAGE="bdx${SUFFIX}sa"              # storage account names: lowercase, <=24 chars, globally unique
FUNCAPP="bdx-${SUFFIX}-func"
PLAN="bdx-${SUFFIX}-plan"
DOCINTEL="bdx-${SUFFIX}-di"
AOAI="bdx-${SUFFIX}-aoai"
AOAI_DEPLOYMENT="gpt-4o"

# ---- resource group ----------------------------------------------------------
az group create --name "$RG" --location "$LOCATION"

# ---- storage (raw-drawings / exports blobs + jobs table) --------------------
az storage account create \
  --name "$STORAGE" --resource-group "$RG" --location "$LOCATION" \
  --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 \
  --allow-blob-public-access false

# ---- Document Intelligence ---------------------------------------------------
az cognitiveservices account create \
  --name "$DOCINTEL" --resource-group "$RG" --location "$LOCATION" \
  --kind FormRecognizer --sku S0 --custom-domain "$DOCINTEL" --yes

# ---- Azure OpenAI + model deployment -----------------------------------------
az cognitiveservices account create \
  --name "$AOAI" --resource-group "$RG" --location "$LOCATION" \
  --kind OpenAI --sku S0 --custom-domain "$AOAI" --yes

az cognitiveservices account deployment create \
  --name "$AOAI" --resource-group "$RG" \
  --deployment-name "$AOAI_DEPLOYMENT" \
  --model-name "gpt-4o" --model-version "2024-08-06" \
  --model-format OpenAI --sku-capacity 10 --sku-name Standard

# ---- Function App (Premium plan — see note below) ----------------------------
az functionapp plan create \
  --name "$PLAN" --resource-group "$RG" --location "$LOCATION" \
  --sku EP1 --is-linux

az functionapp create \
  --name "$FUNCAPP" --resource-group "$RG" --plan "$PLAN" \
  --storage-account "$STORAGE" --runtime python --runtime-version 3.11 \
  --functions-version 4 --os-type Linux
```

**Why Premium (`EP1`), not Consumption:** the Consumption plan's default ~230s function timeout
is tight once you're rasterizing multiple pages and making sequential Azure OpenAI vision calls
per page — a 10-page drawing at the POC's `MAX_PAGES` ceiling can plausibly exceed it. Premium
also avoids cold-start latency skewing your accuracy-timing observations. If you know your test
drawings are small (1–3 pages), Consumption (`az functionapp plan create --sku Y1`) is cheaper and
fine — just watch for `504`s on larger files.

### 2.2 Wire up app settings

```bash
STORAGE_CONN=$(az storage account show-connection-string --name "$STORAGE" --resource-group "$RG" --query connectionString -o tsv)
DOCINTEL_ENDPOINT=$(az cognitiveservices account show --name "$DOCINTEL" --resource-group "$RG" --query properties.endpoint -o tsv)
DOCINTEL_KEY=$(az cognitiveservices account keys list --name "$DOCINTEL" --resource-group "$RG" --query key1 -o tsv)
AOAI_ENDPOINT=$(az cognitiveservices account show --name "$AOAI" --resource-group "$RG" --query properties.endpoint -o tsv)
AOAI_KEY=$(az cognitiveservices account keys list --name "$AOAI" --resource-group "$RG" --query key1 -o tsv)

az functionapp config appsettings set \
  --name "$FUNCAPP" --resource-group "$RG" \
  --settings \
    "AzureWebJobsStorage=$STORAGE_CONN" \
    "DOCUMENT_INTELLIGENCE_ENDPOINT=$DOCINTEL_ENDPOINT" \
    "DOCUMENT_INTELLIGENCE_KEY=$DOCINTEL_KEY" \
    "AZURE_OPENAI_ENDPOINT=$AOAI_ENDPOINT" \
    "AZURE_OPENAI_KEY=$AOAI_KEY" \
    "AZURE_OPENAI_DEPLOYMENT=$AOAI_DEPLOYMENT" \
    "AZURE_OPENAI_API_VERSION=2024-08-01-preview" \
    "TARGET_DPI=300" \
    "MIN_DPI=200" \
    "MAX_PAGES=10"
```

These map 1:1 to [`local.settings.json.example`](local.settings.json.example) and `src/config.py`'s
`Settings.from_env()` — if you add a new required setting to `config.py`, add it here too, or
`extract_drawing` will return a `500 ConfigurationError` naming the missing variable.

> **POC shortcut, flagged:** keys are stored as plain app settings above, not Key Vault
> references. Acceptable for disposable infra holding no real customer data; do **not** carry this
> forward past the POC — see `architecture-full.md` §1.2/§3.3 for the managed-identity + Key Vault
> model the production design uses instead.

---

## 3. Deploy the code

From the `poc/` directory (this file's directory):

```bash
func azure functionapp publish "$FUNCAPP" --python
```

This builds the Python dependencies remotely on Azure (matching `requirements.txt`) and deploys
`function_app.py` + `src/`. Confirm both routes registered:

```bash
az functionapp function list --name "$FUNCAPP" --resource-group "$RG" --query "[].name" -o tsv
# expect: extract_drawing
#         get_drawing_result
```

---

## 4. Smoke test

Grab the function-level key (this app uses `AuthLevel.FUNCTION` — see `architecture-poc.md` §3.3;
there is no user auth):

```bash
FUNC_KEY=$(az functionapp keys list --name "$FUNCAPP" --resource-group "$RG" --query "functionKeys.default" -o tsv)
BASE_URL="https://${FUNCAPP}.azurewebsites.net"

curl -X POST "${BASE_URL}/api/drawings/extract" \
  -H "x-functions-key: ${FUNC_KEY}" \
  -F "file=@/path/to/a/real/ballooned-drawing.pdf"
```

Expect a `200` with the JSON shape documented in `architecture-poc.md` §3.2 (`jobId`,
`balloonCountDetected`, `balloonCountExtracted`, `balloonCountMismatch`, `balloons[]`,
`exportUrl`). Then:

```bash
curl "${BASE_URL}/api/drawings/<jobId>?code=${FUNC_KEY}"
```

should return the same job's status. A `404` here with a fresh `jobId` almost always means the
Table Storage connection string is wrong, not that the job failed — check `AzureWebJobsStorage`
first.

**If the first call times out or 502s:** check Document Intelligence and Azure OpenAI endpoint/key
pairing (easy to transpose), and confirm the `AZURE_OPENAI_DEPLOYMENT` value matches the
deployment *name* you chose in §2.1 (`gpt-4o` here), not the underlying model name — they can
differ if you name deployments differently.

---

## 5. Monitoring

```bash
func azure functionapp logstream "$FUNCAPP" --resource-group "$RG"
```

for live tailing during a test run, or query Application Insights (auto-linked if you used the
Bicep template in Appendix A; wire one up manually via `az monitor app-insights component create`
if you used the pure-CLI path and want it) for structured queries, e.g. extraction failures:

```kusto
traces
| where message has "Extraction gave up after"
| order by timestamp desc
```

---

## 6. Cost awareness (POC-specific)

This is a low-volume, short-lived deployment, but three things can surprise you:

- **Azure OpenAI vision calls are the dominant cost.** Every page sends a full-resolution PNG
  (at `TARGET_DPI`, default 300) as an image input plus the repair-loop re-prompt on failures —
  a 10-page drawing that needs one repair per page is ~20 model calls. Keep `MAX_PAGES` low while
  iterating on prompts.
- **Document Intelligence S0** is pay-per-page; fine at POC volume, but don't loop a large test
  corpus through it repeatedly without noticing.
- **EP1 Premium plan bills per-second regardless of traffic** while it's running (unlike
  Consumption). Stop or delete it (§7) between test sessions if you're pausing for more than a day.

---

## 7. Teardown

Matches `architecture-poc.md` §4.3 ("the entire resource group is expected to be deleted once the
extraction-accuracy question is answered"):

```bash
az group delete --name "$RG" --yes --no-wait
```

Blob lifecycle management (30-day auto-delete) is a safety net if you forget this step, per
`architecture-poc.md` §4.3 — it is not a substitute for actually tearing down the Premium plan,
which is the main cost driver if left running.

---

## 8. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `500 ConfigurationError: Missing required environment variable: X` | An app setting from §2.2 wasn't set, or was set with a typo'd name — compare against `src/config.py`'s `Settings.from_env()`. |
| `400 UnsupportedFileType` on a file you know is a PDF | The client sending the request didn't set `Content-Type` on the multipart part correctly (some HTTP clients need it forced) — check with `curl -v`. |
| `422 QualityThresholdNotMet` immediately on upload | The source image's embedded DPI metadata is below `MIN_DPI` (default 200) — either the scan is genuinely low-res, or (common with some scanners) DPI metadata is missing/wrong and PIL fell back to a default; inspect with `identify -verbose` (ImageMagick) or `exiftool`. |
| `502 ExtractionServiceError` on every call | Endpoint/key mismatch between Document Intelligence and Azure OpenAI, wrong API version, or the `gpt-4o` deployment doesn't support `response_format: json_schema` structured outputs at the pinned `AZURE_OPENAI_API_VERSION` — bump the API version. |
| Function times out around 230s on multi-page files | You're on a Consumption plan — move to `EP1` (§2.1) or reduce `MAX_PAGES`. |
| `func azure functionapp publish` succeeds but functions don't show up | Remote build may have failed silently — re-run with `--build remote --verbose` and check the output for a `pip install` failure (usually a `requirements.txt` pin unavailable for the deployed Python version). |

---

## Appendix A — One-shot provisioning with Bicep

Equivalent to §2.1–2.2 in a single deployable template. Save as `poc/infra/main.bicep`:

```bicep
@description('Short, unique suffix appended to every resource name (e.g. your initials + date).')
param suffix string

@description('Azure region. Must support both Document Intelligence and Azure OpenAI.')
param location string = 'eastus'

@description('Azure OpenAI deployment name the app will call.')
param openAiDeploymentName string = 'gpt-4o'

@description('Underlying Azure OpenAI model to deploy.')
param openAiModelName string = 'gpt-4o'

@description('Model version — re-check availability in your region before deploying.')
param openAiModelVersion string = '2024-08-06'

var storageAccountName = toLower('bdx${suffix}sa')
var functionAppName = 'bdx-${suffix}-func'
var appServicePlanName = 'bdx-${suffix}-plan'
var docIntelName = 'bdx-${suffix}-di'
var openAiName = 'bdx-${suffix}-aoai'
var appInsightsName = 'bdx-${suffix}-ai'

resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: { Application_Type: 'web' }
}

resource plan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: appServicePlanName
  location: location
  sku: { name: 'EP1', tier: 'ElasticPremium' }
}

resource docIntel 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: docIntelName
  location: location
  kind: 'FormRecognizer'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: docIntelName
    publicNetworkAccess: 'Enabled'
  }
}

resource openAi 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: openAiName
  location: location
  kind: 'OpenAI'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: openAiName
    publicNetworkAccess: 'Enabled'
  }
}

resource openAiDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  parent: openAi
  name: openAiDeploymentName
  sku: { name: 'Standard', capacity: 10 }
  properties: {
    model: {
      format: 'OpenAI'
      name: openAiModelName
      version: openAiModelVersion
    }
  }
}

resource functionApp 'Microsoft.Web/sites@2023-01-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      appSettings: [
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'AzureWebJobsStorage', value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=core.windows.net' }
        { name: 'APPINSIGHTS_INSTRUMENTATIONKEY', value: appInsights.properties.InstrumentationKey }
        { name: 'DOCUMENT_INTELLIGENCE_ENDPOINT', value: docIntel.properties.endpoint }
        { name: 'DOCUMENT_INTELLIGENCE_KEY', value: docIntel.listKeys().key1 }
        { name: 'AZURE_OPENAI_ENDPOINT', value: openAi.properties.endpoint }
        { name: 'AZURE_OPENAI_KEY', value: openAi.listKeys().key1 }
        { name: 'AZURE_OPENAI_DEPLOYMENT', value: openAiDeploymentName }
        { name: 'AZURE_OPENAI_API_VERSION', value: '2024-08-01-preview' }
        { name: 'TARGET_DPI', value: '300' }
        { name: 'MIN_DPI', value: '200' }
        { name: 'MAX_PAGES', value: '10' }
      ]
    }
  }
}

output functionAppName string = functionApp.name
output functionAppHostName string = 'https://${functionApp.properties.defaultHostName}'
```

Deploy it:

```bash
az group create --name bdx-poc01-rg --location eastus
az deployment group create \
  --resource-group bdx-poc01-rg \
  --template-file poc/infra/main.bicep \
  --parameters suffix=poc01
func azure functionapp publish "$(az deployment group show -g bdx-poc01-rg -n main --query properties.outputs.functionAppName.value -o tsv)" --python
```

Same caveat as §2.2: this template puts keys directly in app settings for provisioning speed.
Swap the four `*_KEY`/`AzureWebJobsStorage` app settings for Key Vault references + a system-
assigned managed identity before this touches anything beyond a disposable test resource group.
