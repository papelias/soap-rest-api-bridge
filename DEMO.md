# Two-minute demo plan

A local MVP demonstration, not a client or production deployment. Do setup and the full test run **before** the two-minute clock. Keep `.env` off screen.

## Preparation, outside the clock

From the repository root:

```powershell
uv sync --frozen --python 3.11
uv --offline run --frozen pytest -q
```

Run the offline test suite before the demo. Duration varies and is outside the demo.

Configure the key and both model IDs privately in `.env`. Terminal 1:

```powershell
$env:SOAP_WSDL_URL = "https://webservices.oorsprong.org/websamples.countryinfo/CountryInfoService.wso?WSDL"
$env:FACADE_BASE_URL = "http://127.0.0.1:8000"
uv run python -m soap_rest_ai.soap.validate_live
uv run uvicorn soap_rest_ai.main:app --host 127.0.0.1 --port 8000
```

Use terminal 2 below. Open README preview at the diagram and keep the saved rerun report ready. Model wording, latency, and upstream availability can vary; do not promise a verbatim answer.

## Exact sequence

### 0:00–0:12 — Problem and architecture

Visible: README diagram and five-operation scope.

Say: “This MVP makes a public SOAP service usable through REST and natural-language questions. The model proposes a read; application code decides whether it may execute. This is a portfolio scenario, not a production client deployment.”

### 0:12–0:25 — Health

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health | ConvertTo-Json
```

Visible: `{"status":"ok"}`.

Say: “One FastAPI app owns the clients. Startup and health work without either upstream service being configured.”

### 0:25–0:40 — Typed REST contract

```powershell
Invoke-RestMethod http://127.0.0.1:8000/countries/PH/currency -TimeoutSec 10 | ConvertTo-Json -Depth 8
```

Visible: `operation: CountryCurrency`, `iso_code: PH`, and currency `iso_code: PHP`.

Say: “This route maps one-to-one to a SOAP read. The adapter handles Zeep and XML; the response exposes JSON fields.”

### 0:40–1:00 — Question and provenance

```powershell
$body = @{ question = "What is the capital of Japan?" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/ask -ContentType application/json -Body $body -TimeoutSec 15 | ConvertTo-Json -Depth 8
```

Visible on success: an answer identifying Tokyo and provenance with `CapitalCity`, `sCountryISOCode: JP`, and `/countries/JP/capital`. If it fails or exceeds the demo timeout, use the explicitly recorded fallback below rather than waiting through the timebox.

Say: “The router selects an operation and code. NL calls REST over HTTP, then a second model call summarizes the result. Code builds provenance from the validated execution plan.”

### 1:00–1:25 — Deterministic guard proof

```powershell
uv --offline run --frozen pytest 'tests/test_ask.py::test_every_unapproved_catalog_operation_is_blocked_by_code[CountryFlag]' -q
```

Visible: **1 passed**. This offline test forces a `CountryFlag` proposal, adds it to configuration, and still asserts 403 plus zero facade calls. It mocks provider/HTTP; it is not a live-model attack demo.

Say: “CountryFlag exists in the WSDL, but discovery is not permission. This test forces the unsafe proposal and verifies that code blocks it before HTTP, even if configuration tries to expand permission.”

### 1:25–1:45 — Measured results and regressions

```powershell
$run = Get-Content data/evals/runs/improved-01/results.json -Raw | ConvertFrom-Json
$run.metrics | Select-Object combined_accuracy,end_to_end_success,safety_success | ConvertTo-Json -Depth 5
$run.comparison.regressions
```

Visible: combined accuracy `20/20`, end-to-end contract success `34/37`, safety `10/10`; regressions `normal-17`, `normal-18`, `failure-07`.

Say: “The fixed set reached 100% combined routing accuracy, but end-to-end success stayed at 34 of 37. I retained the baseline and reported answer-generation failures and a multi-country refusal regression.”

### 1:45–2:00 — Limits and next decision

Visible: README limitations or report failures.

Say: “The guard protects execution scope; it does not prove answer quality or complete intent handling. Before a production proposal, I would resolve these failures, expand evaluation, and agree data-quality, access, privacy, and service-level requirements.”

Stop the server with Ctrl+C afterward.

## If a live call fails

Do not present saved output as live. Say: “The live path is unavailable in this attempt; here is the recorded evaluation response and provenance.” Show:

```powershell
$run = Get-Content data/evals/runs/improved-01/results.json -Raw | ConvertFrom-Json
$run.cases | Where-Object id -eq 'normal-01' | Select-Object -ExpandProperty response | ConvertTo-Json -Depth 8
```

If SOAP is unavailable, show the saved result/offline tests rather than claiming REST succeeded. Further questions, after the timed demo:

- “What currency does the Philippines use?”
- “What is Germany's international phone code?”
- “Give me full information about Canada.”
- “Which countries use EUR?” — disclose its recorded answer-generation regression rather than guaranteeing success.
