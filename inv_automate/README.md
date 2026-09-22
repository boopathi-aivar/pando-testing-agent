# Invoice Automate

Standalone agentic invoice extraction: PDF + JSON schema + **sample carrier prompt** (+ optional **field mapping .xlsx**) → structured JSON, with **AWS Bedrock Sonnet** (default) or local Ollama, plus optional Docling HITL retry.

## Pipeline

```
PDF + schema + sample extraction prompt (one existing carrier)
  [+ optional field mapping .xlsx]
  → page split (PyMuPDF)
  → per-page classify (MarkItDown digital / RapidOCR scanned)
  → merge text
  → LLM generates session_prompt (schema + sample style [+ mapping locations])
  → schema coverage + schema↔mapping reports
  → extract with session_prompt (Bedrock / Ollama)
  → validate (+ grounding score)
  → if low accuracy: HITL → optional Docling → re-extract
  → UI: KV field table → mark wrong / generic note → refine prompt + remap (+ prompt diff)
  → prompt Q&A on result
```

**Sample prompt:** required. Paste a single existing carrier extraction prompt as a **style reference**. Do not paste an entire multi-carrier prompt library.

**Field mapping (optional):** `.xlsx` with **exactly one sheet** and headers:

`Table Name` | `Column Name` | `Field Location in Actual Invoice` | `Remarks`

- JSON schema defines output fields.
- Mapping adds table-grouped locations/remarks (and `Default: …` rules).
- Empty Remarks OK if Location is present.
- Rows with neither Location nor Remarks are skipped.
- After prompt generation, the UI shows schema↔mapping match (schema fields with no row, mapping-only columns, mapped-without-location).

**Coverage:** after prompt gen (and after feedback refine), the API/UI report which schema fields are missing from the session prompt.

**Prompt diff:** after refine & remap, the response includes a unified diff of prompt changes.

## Prerequisites

1. **AWS credentials** via CLI (not in `.env`):

```bash
aws configure
aws sts get-caller-identity
```

2. **Model id in `.env`** — copy from `.env.example` and set your Sonnet 4.5 inference profile ARN.

3. **Python 3.10+** (3.11 recommended; `markitdown` requires ≥3.10)

4. Optional: **Ollama** if `LLM_PROVIDER=ollama`

## Setup

```bash
cd inv_automate
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit AWS_BEDROCK_INFERENCE_GLOBAL_ID

# Optional Docling fallback:
# pip install docling
```

## Run

```bash
cd inv_automate
source .venv/bin/activate
python main.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Config (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `bedrock` | `bedrock` or `ollama` |
| `AWS_REGION` | `us-east-1` | Bedrock region |
| `AWS_BEDROCK_INFERENCE_GLOBAL_ID` | — | Sonnet inference profile ARN / model id |
| `BEDROCK_MODEL_ARN` | — | Alias if the global id env is unset |
| `OLLAMA_MODEL` | `gemma4:e2b` | Used when provider is ollama |
| `ACCURACY_THRESHOLD` | `0.75` | Below this → HITL Docling offer |
| `MAX_PAGES_V1` | `15` | Hard page cap |
| `PORT` | `8000` | HTTP port |

Access keys stay in `~/.aws` from `aws configure`. Do not put them in `.env`.

## API

- `POST /api/v1/invoice/extract` — multipart: `file` (PDF), `schema` (JSON string), `sample_prompt` (required), `field_mapping` (optional `.xlsx`)
- `GET /api/v1/invoice/field-mapping/headers` — expected Excel headers + rules
- `POST /api/v1/invoice/feedback` — `{ "thread_id", "retry": true|false }` (Docling HITL when accuracy is low)
- `POST /api/v1/invoice/docling-retry` — `{ "thread_id" }` (opt-in Docling re-OCR + re-extract anytime after extract)
- `POST /api/v1/invoice/correct` — `{ "thread_id", "corrections": [{path, current_value, note}], "generic_note": "...", "refine_prompt": true }`  
  (`corrections` and/or `generic_note` required)
- `POST /api/v1/invoice/prompt` — `{ "thread_id", "prompt" }`
- `GET /api/health`

Extract/correct responses include `session_prompt`, `fields`, `field_mapping_used`, `prompt_coverage`, `mapping_match`, and (after correct) `prompt_diff`.
