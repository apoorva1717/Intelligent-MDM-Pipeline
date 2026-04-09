# SAP Customer Master Data Name Enrichment API

Intelligent enrichment of SAP customer name fields for the Bruker Corporation MDM (Master Data Management) pipeline. Resolves incomplete, abbreviated, or incorrectly formatted institution and company names through a multi-tier approach.

## Architecture

The API uses a tiered enrichment strategy:

| Tier | Method | Use Case |
|------|--------|----------|
| **Tier 1** | ROR API | Resolve acronyms, match official institution names, fuzzy-match child organisations |
| **Tier 2A** | Contact Person Lookup | Find/verify department via contact's faculty page (research institutions only) |
| **Tier 2B** | Department Search | SERP search + LLM extraction for department/division names |
| **Tier 3** | LLM Inference | Last resort — always flagged for manual review |

### Record Classification

Records are classified as `research_institution` or `company` based on keywords in Name 1 (e.g. "University", "Hospital", "Institut"). This affects which tiers and modes are available.

### Tier 2A Modes

- **Mode A (Population)**: Name 2 is null — discover department from contact's page
- **Mode B (Verification)**: Name 2 exists — verify/correct against contact's page

## Setup

### Prerequisites

- Python 3.11+
- Azure OpenAI API access (or use mock mode)

### Installation

```bash
cd enrichment_api
pip install -r requirements.txt

# For development/testing:
pip install -r requirements-dev.txt
```

### Configuration

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Required:
- `AZURE_OPENAI_API_KEY` — your Azure OpenAI key
- `AZURE_OPENAI_ENDPOINT` — e.g. `https://your-resource.openai.azure.com/`
- `AZURE_OPENAI_DEPLOYMENT` — model deployment name (default: `gpt-4o`)

Optional:
- `SERPAPI_KEY` — leave blank to use DuckDuckGo as fallback
- `MOCK_EXTERNAL_CALLS=true` — use mock clients (no API keys needed)

## Running Locally

### Standard (with API keys)

```bash
ENV=local python main.py
# or
ENV=local uvicorn api.app:app --reload --port 8000
```

### Mock Mode (no API keys needed)

```bash
ENV=local MOCK_EXTERNAL_CALLS=true python main.py
```

## API Endpoints

### Health Check

```bash
curl http://localhost:8000/health
```

### Tier Configuration

```bash
curl http://localhost:8000/tiers
```

### Enrichment

**Research institution, name2 null, contact present (Mode A):**

```bash
curl -X POST http://localhost:8000/enrich \
  -H "Content-Type: application/json" \
  -d '{"records":[{"record_id":"BSP_001",
  "name1":"Massachusetts Institute of Technology",
  "name2":null,"contact":"Dr. Jane Smith",
  "city":"Cambridge","state":"MA","country":"US"}],
  "options":{"max_concurrency":1}}'
```

**Research institution, name2 wrong format, contact present (Mode B):**

```bash
curl -X POST http://localhost:8000/enrich \
  -H "Content-Type: application/json" \
  -d '{"records":[{"record_id":"BSP_002",
  "name1":"Massachusetts Institute of Technology",
  "name2":"Dept of AI","contact":"Dr. Jane Smith",
  "city":"Cambridge","state":"MA","country":"US"}],
  "options":{"max_concurrency":1}}'
```

**Research institution, no contact, name2 present (Tier 2B):**

```bash
curl -X POST http://localhost:8000/enrich \
  -H "Content-Type: application/json" \
  -d '{"records":[{"record_id":"BSP_003",
  "name1":"Stanford University",
  "name2":"Chemistry Department",
  "city":"Stanford","state":"CA","country":"US"}],
  "options":{"max_concurrency":1}}'
```

**Company with name2 (skips 2A):**

```bash
curl -X POST http://localhost:8000/enrich \
  -H "Content-Type: application/json" \
  -d '{"records":[{"record_id":"BSP_004",
  "name1":"Pfizer Inc","name2":"Analytical Sciences",
  "city":"New York","state":"NY","country":"US"}],
  "options":{"max_concurrency":1}}'
```

**Acronym name1 (Tier 1 resolution):**

```bash
curl -X POST http://localhost:8000/enrich \
  -H "Content-Type: application/json" \
  -d '{"records":[{"record_id":"BSP_005",
  "name1":"UCLA",
  "city":"Los Angeles","state":"CA","country":"US"}],
  "options":{"max_concurrency":1}}'
```

**Blank name2, no contact (Tier 3):**

```bash
curl -X POST http://localhost:8000/enrich \
  -H "Content-Type: application/json" \
  -d '{"records":[{"record_id":"BSP_006",
  "name1":"Some Unknown Lab",
  "city":"Boston","state":"MA","country":"US"}],
  "options":{"max_concurrency":1}}'
```

**Batch of 3 mixed records:**

```bash
curl -X POST http://localhost:8000/enrich \
  -H "Content-Type: application/json" \
  -d '{"records":[
  {"record_id":"B1","name1":"MIT","name2":"Department of Physics"},
  {"record_id":"B2","name1":"Pfizer Inc","name2":"R&D"},
  {"record_id":"B3","name1":"UCLA","contact":"Dr. John Doe"}],
  "options":{"max_concurrency":3}}'
```

## Testing

### Pytest

```bash
# Run all tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=enrichment --cov-report=term-missing

# Single test file
pytest tests/test_orchestrator.py -v
```

### Local Integration Test

```bash
# Mock mode (no API keys)
python scripts/test_local.py --mock

# Live mode (real APIs)
python scripts/test_local.py --live

# Single fixture
python scripts/test_local.py --fixture acronym_name1.json
```

## Azure Function Deployment

The application deploys as an Azure Function v2 using ASGI integration. The `function_app.py` entry point wraps the same FastAPI app.

```
ADF Web Activity → POST /enrich → Azure Function → FastAPI → Enrichment Pipeline
```

### ADF Integration

1. DATAshaper stored procedure extracts batch from Validation table
2. ADF Web Activity POSTs to `/enrich`
3. ADF receives JSON response
4. Second stored procedure writes enriched values + issues back

### DATAshaper Mapping

| enrichment_status | DATAshaper Issue Severity |
|---|---|
| `enriched` | No issue (auto-applied) |
| `verified` | Info issue (confirmed correct) |
| `unresolved` | Warning issue (manual review) |
| `failed` | Error issue (process failed) |

## Project Structure

```
enrichment_api/
├── main.py                   # uvicorn local entry point
├── function_app.py           # Azure Function v2 ASGI entry point
├── config.py                 # env loading, dotenv for local
├── api/
│   ├── app.py                # FastAPI app object (shared)
│   ├── routes.py             # route definitions
│   ├── models.py             # pydantic v2 models
│   └── middleware.py         # JSON logging, timing, errors
├── enrichment/
│   ├── orchestrator.py       # tier escalation, Mode A vs B
│   ├── classifier.py         # institution vs company
│   ├── tier1_ror.py          # ROR API + child matching
│   ├── tier2a_contact.py     # contact lookup, both modes
│   ├── tier2b_dept.py        # dept SERP search
│   ├── tier3_llm.py          # LLM inference
│   └── confidence.py         # scoring, flag rules, status
├── search/
│   ├── base.py               # abstract search interface
│   ├── serpapi_client.py      # SerpAPI implementation
│   ├── duckduckgo_client.py   # DuckDuckGo fallback
│   └── page_fetcher.py       # requests + BeautifulSoup
├── llm/
│   ├── azure_openai.py       # async OpenAI client + retries
│   └── prompts.py            # all prompts as module constants
├── utils/
│   ├── text_utils.py         # cleaning, domain extraction
│   └── cache.py              # per-batch in-memory cache
├── tests/
│   ├── conftest.py           # fixtures, mock injection
│   ├── test_*.py             # pytest test files
│   ├── mocks/                # mock clients for DI
│   └── fixtures/             # JSON test data
├── scripts/
│   └── test_local.py         # local integration test
├── requirements.txt
├── requirements-dev.txt
├── host.json
├── .env.example
└── .gitignore
```
