# Pando Invoice Testing Agent — Architecture Guide

> This document explains what the system does, how it is structured, and how all the pieces connect.
> It is written for someone reading the codebase for the first time.

---

## Table of Contents

1. [What Is This System?](#1-what-is-this-system)
2. [The Big Picture](#2-the-big-picture)
3. [How to Run the App](#3-how-to-run-the-app)
4. [How a Test Run Works](#4-how-a-test-run-works)
   - [Mode A — You Click "Run Test" in the UI](#mode-a--you-click-run-test-in-the-ui)
   - [Mode B — Lambda Pushes Data Automatically](#mode-b--lambda-pushes-data-automatically)
5. [Backend Deep Dive](#5-backend-deep-dive)
   - [Folder Structure](#folder-structure)
   - [API Routes](#api-routes)
   - [The AI Agent Pipeline](#the-ai-agent-pipeline)
6. [Frontend Deep Dive](#6-frontend-deep-dive)
7. [Database Design](#7-database-design)
8. [AWS Services Used](#8-aws-services-used)
9. [Authentication & Security](#9-authentication--security)
10. [Tech Stack at a Glance](#10-tech-stack-at-a-glance)
7. [AWS Services Used](#7-aws-services-used)
8. [Authentication & Security](#8-authentication--security)
9. [Tech Stack at a Glance](#9-tech-stack-at-a-glance)

---

## 1. What Is This System?

### The Problem

Pando runs an **AWS Lambda function** (a serverless worker) that reads freight invoices from carriers, extracts structured data from them (invoice number, amounts, charge codes, dates, etc.), and sends that data to an internal API.

The extracted data must be **accurate**. If the Lambda extracts a wrong amount or misses a charge code, it causes billing errors downstream. Manually checking every invoice is impractical.

### The Solution

The **Pando Invoice Testing Agent** is an automated QA platform that:

1. Intercepts the data the Lambda extracted from an invoice.
2. Compares it against the **actual invoice PDF** and a **vendor-specific mapping spreadsheet** (Excel file stored in S3).
3. Uses an **AI agent** (Claude via AWS Bedrock) to judge each field — correct, wrong, or missing.
4. Produces a **scored test result** (0–100%) and a list of actionable suggestions for fixing the Lambda's prompt.
5. Displays everything in a **web dashboard** so the team can track quality over time.

### Key Concepts

| Term | Plain meaning |
|---|---|
| **Project** | A named configuration that links a Lambda log group, an S3 Excel mapping file, and scoring rules together |
| **Test Run** | One execution of the validation pipeline for a project |
| **Invoice Payload** | The JSON object the Lambda extracted from an invoice and sent to the Pando API |
| **Field Mapping** | An Excel spreadsheet that defines, per carrier, which fields should appear and what values are expected |
| **Strands Agent** | An AI agent built with the Strands Agents SDK that can call tools and reason about data |
| **AWS Bedrock** | Amazon's managed service for running large language models (this project uses Claude Sonnet) |

---

## 2. The Big Picture

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │                         WEB BROWSER                                  │
 │  React dashboard — Dashboard, Projects, Configure, Results, Settings │
 └──────────────────────────────┬───────────────────────────────────────┘
                                │ HTTPS  (JWT Bearer token)
                                ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │                     FASTAPI BACKEND  (Python)                        │
 │                                                                      │
 │   /auth   /projects   /results   /jobs   /intake                     │
 │                            │               │                         │
 │                    ┌───────┴───────┐ ┌─────┴────────┐               │
 │                    │ Agent Runner  │ │    Intake     │               │
 │                    │ (background   │ │    Processor  │               │
 │                    │  thread)      │ │ (background   │               │
 │                    └───────┬───────┘ │  thread)      │               │
 │                            │         └─────┬──────────┘               │
 │                            └──────┬────────┘                         │
 │                                   ▼                                  │
 │                        ┌──────────────────┐                          │
 │                        │   Orchestrator   │  ← coordinates agents    │
 │                        └──┬───────────┬───┘                          │
 │                           │           │                              │
 │              ┌────────────┘   ┌───────┘                             │
 │              ▼                ▼                    ▼                 │
 │  ┌─────────────────┐  ┌──────────────┐  ┌───────────────────┐       │
 │  │  Log Analyzer   │  │   Input      │  │ Payload Validator │       │
 │  │  (AI + rules)   │  │  Collector   │  │  (AI agent)       │       │
 │  └────────┬────────┘  └──────┬───────┘  └───────────────────┘       │
 └───────────┼──────────────────┼──────────────────────────────────────┘
             │                  │
             ▼                  ▼
   ┌──────────────────┐   ┌───────────────────┐
   │  AWS CloudWatch  │   │     AWS S3        │
   │  (Lambda logs)   │   │  (Excel, PDF)     │
   └──────────────────┘   └───────────────────┘

   ┌──────────────────────┐     ┌──────────────────────────┐
   │    AWS Bedrock        │     │    MongoDB Atlas          │
   │  Claude Sonnet 4.6   │     │  projects / results /    │
   │  (LLM reasoning)     │     │  jobs                    │
   └──────────────────────┘     └──────────────────────────┘

   ┌─────────────────────────────────────────────────┐
   │  AWS Lambda  (Invoice Processor — external)     │
   │  Automatically POSTs results → POST /api/intake │
   └─────────────────────────────────────────────────┘
```

---

## 3. How to Run the App

### Prerequisites

Make sure the following are installed on your machine before starting:

- **Python 3.11+** — for the backend
- **Node.js 18+** — for the frontend
- **A running MongoDB Atlas cluster** — connection string goes in `.env`
- **AWS credentials** — access key + secret with S3, CloudWatch, and Bedrock permissions

---

### Step 1 — Configure environment variables

The backend reads all secrets from `backend/.env`. A template is provided at `backend/.env.example`.

Copy the template and fill in your values:

```bash
cp backend/.env.example backend/.env
```

Then open `backend/.env` and fill in:

```
MONGODB_URL=mongodb+srv://<username>:<password>@<cluster>.mongodb.net/?appName=<AppName>
MONGODB_DB=pando_testing_agent
JWT_SECRET_KEY=change-me-in-production

AWS_REGION=ap-south-1
AWS_ACCESS_KEY_ID=your-aws-access-key
AWS_SECRET_ACCESS_KEY=your-aws-secret-key

INTAKE_API_KEY=your-shared-secret-with-lambda
```

---

### Step 2 — Install dependencies (first time only)

**Backend:**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**Frontend:**

```bash
cd frontend
npm install
```

Or install both at once from the project root:

```bash
make install
```

---

### Step 3 — Start the servers

You need **two terminals** open at the same time — one for the backend, one for the frontend.

**Terminal 1 — Backend (runs on port 3001):**

```bash
cd backend
source .venv/bin/activate
uvicorn main:app --reload --port 3001
```

**Terminal 2 — Frontend (runs on port 5173):**

```bash
cd frontend
npm run dev
```

Or start both together from the project root using Make:

```bash
make dev
```

---

### Step 4 — Open the app

Visit **http://localhost:5173** in your browser.

Log in with the default admin credentials:

| Field | Value |
|---|---|
| Email | `pando@aivar.tech` |
| Password | `pando@123` |

> The frontend automatically proxies all `/api/*` requests to `http://localhost:3001`
> (configured in `frontend/vite.config.js`), so there are no CORS issues during development.

---

### What healthy startup looks like

When the backend starts successfully, you should see this in the terminal:

```
────────────────────────────────────────────────────────────
  MongoDB connection check
  URL : mongodb+srv://...
  DB  : pando_testing_agent
────────────────────────────────────────────────────────────
  ✓  Connected  (MongoDB 7.x  |  host: ...)
────────────────────────────────────────────────────────────

INFO:     Uvicorn running on http://127.0.0.1:3001 (Press CTRL+C to quit)
INFO:     Started reloader process
```

If you see `✗ FAILED`, check that `MONGODB_URL` in `backend/.env` is correct and that your IP is whitelisted in MongoDB Atlas.

---

### Quick reference — all commands

| Task | Command |
|---|---|
| Install everything | `make install` |
| Start backend only | `make dev-backend` |
| Start frontend only | `make dev-frontend` |
| Start both together | `make dev` |
| Backend URL | `http://localhost:3001` |
| Frontend URL | `http://localhost:5173` |
| API docs (Swagger) | `http://localhost:3001/docs` |

---

## 4. How a Test Run Works

There are two ways a test run starts. Both end at the same place: a `TestResult` record saved to MongoDB and visible in the dashboard.

---

### Mode A — You Click "Run Test" in the UI

This is the **pull mode**. The backend goes and fetches data itself.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Step 1 — User triggers the run                                     │
│                                                                     │
│  Browser  ──POST /api/projects/{id}/run-test──►  FastAPI            │
│                                                      │              │
│                    FastAPI responds immediately:     │              │
│  Browser  ◄── 202 Accepted  +  job_id ──────────────┘              │
│                                                                     │
│  (The browser now polls GET /api/jobs/{job_id}/status every few     │
│   seconds to show progress in the "Run Test" modal)                 │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Step 2 — Agent Runner (background thread)                          │
│                                                                     │
│  A background thread starts immediately after the 202 response.     │
│  It tracks 5 progress steps in MongoDB so the UI can show them:     │
│                                                                     │
│   [1] Collecting inputs from S3                                     │
│   [2] Querying CloudWatch logs                                      │
│   [3] Validating payload fields                                     │
│   [4] Computing scores and suggestions                              │
│   [5] Saving result to database                                     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Step 3 — Orchestrator runs the pipeline                            │
│                                                                     │
│  3a. Log Analyzer                                                   │
│      Reads the Lambda's CloudWatch log group for the last N hours.  │
│      Finds every invoice processing run in the logs.                │
│      Extracts: invoice payload JSON, HTTP status code, errors,      │
│      execution time, and the Lambda's LLM prompt text.              │
│                                                                     │
│  3b. Input Collector (runs once per invoice)                        │
│      Downloads the S3 files configured for this project:            │
│        • Excel workbook → field mapping rules + charge code table   │
│        • Invoice PDF    → converted to Markdown (ground truth)      │
│                                                                     │
│  3c. Payload Validator (AI agent)                                   │
│      Claude receives:                                               │
│        • The extracted invoice payload from logs                    │
│        • The Excel field mapping for this carrier                   │
│        • The Excel charge mapping for this carrier                  │
│        • The actual invoice PDF (as text)                           │
│        • Any charge rules embedded in the Lambda's own prompt       │
│      Claude decides for each field: correct / wrong / missing.      │
│      Claude also produces suggestions to improve the Lambda prompt. │
│                                                                     │
│  3d. Save result                                                    │
│      TestResult document saved to MongoDB.                          │
│      Project's last_tested + last_score updated.                    │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Step 4 — UI shows results                                          │
│                                                                     │
│  Browser polls job status → sees "complete" → loads results page.  │
│  Shows: overall score, per-field status, suggestions, raw payload.  │
└─────────────────────────────────────────────────────────────────────┘
```

---

### Mode B — Lambda Pushes Data Automatically

This is the **push mode**. The Lambda sends its output here directly after every invoice processing run, without any human action.

```
┌─────────────────────────────────────────────────────────────────────┐
│  AWS Lambda finishes processing an invoice                          │
│                                                                     │
│  Lambda  ──POST /api/intake──►  FastAPI                             │
│           Header: X-Intake-Key: <shared secret>                     │
│           Body: {                                                   │
│             project_id / s3_bucket / log_group,  ← for routing      │
│             invoice_number,                                         │
│             payload,          ← extracted invoice data              │
│             llm_response,     ← what the Lambda's LLM returned      │
│             prompt,           ← the prompt that was used            │
│             execution_duration_ms, cold_start,                      │
│             errors, warnings, api_status                            │
│           }                                                         │
│                                                                     │
│  FastAPI responds immediately: 202 Accepted + job_id                │
│  (Lambda is fire-and-forget — it must not wait for validation)      │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Intake Processor runs in a background thread                       │
│                                                                     │
│  1. Find the project:                                               │
│       Try project_id first (exact) → then s3_bucket (fuzzy match)  │
│       → then log_group (exact)                                      │
│                                                                     │
│  2. If the Lambda reported an API error (status >= 400):            │
│       Record a "failed" result without running AI validation.        │
│                                                                     │
│  3. Otherwise:                                                      │
│       Download S3 mapping files (Excel + invoice PDF)               │
│       Run Payload Validator AI agent                                │
│       Save TestResult to MongoDB                                    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. Backend Deep Dive

### Folder Structure

```
backend/
│
├── main.py                  ← FastAPI app entry point, CORS config
├── config.py                ← All environment variables, AWS session factory
├── database.py              ← MongoDB connection, collection helpers, index setup
├── agent_runner.py          ← Background thread wrapper + step-progress tracker
├── seed.py                  ← Populates empty DB with demo project on first start
│
├── agents/                  ← The AI pipeline
│   ├── orchestrator.py      ← Coordinates all agents for a pull-mode test run
│   ├── input_collector.py   ← Downloads files from S3, parses Excel and PDF
│   ├── log_analyzer.py      ← Reads CloudWatch logs, extracts invoice data
│   ├── payload_validator.py ← AI agent that scores each invoice field
│   └── intake_processor.py ← Runs the pipeline when Lambda pushes data (push mode)
│
├── routers/                 ← HTTP route handlers (one file per feature area)
│   ├── auth.py              ← POST /auth/login, GET /auth/me
│   ├── projects.py          ← CRUD for project configurations
│   ├── results.py           ← Query and filter test results
│   ├── jobs.py              ← Trigger a test run, poll job status
│   └── intake.py            ← Webhook endpoint for Lambda push events
│
├── models/                  ← Pydantic data shapes (request/response types)
│   ├── project.py           ← ProjectConfig, FileSlot, ScoringWeights
│   ├── result.py            ← TestResult, FieldValidation, LogSummary
│   ├── auth.py              ← LoginRequest, TokenResponse
│   └── intake.py            ← IntakePayload (what Lambda sends to /intake)
│
├── services/                ← Low-level AWS / file-format wrappers (no AI)
│   ├── s3.py                ← Download text and binary files from S3
│   ├── cloudwatch.py        ← Query CloudWatch Logs, parse invoice blocks
│   ├── excel_parser.py      ← Read field mapping and charge mapping from .xlsx
│   └── pdf_parser.py        ← Convert PDF bytes → Markdown text (via PyMuPDF)
│
└── tools/                   ← Functions exposed as tools to AI agents
    ├── mongodb_tools.py     ← get_project_config, save_test_result, etc.
    ├── cloudwatch_tools.py  ← search_cloudwatch_logs, get_all_invoice_payloads
    └── s3_tools.py          ← fetch_s3_file, check_s3_file_exists
```

> **Why a `tools/` folder?**
> The Strands Agents SDK lets an AI agent call Python functions marked with `@tool`.
> The `tools/` folder holds these decorated functions. The agent decides on its own
> which tools to call and in what order.

---

### API Routes

All routes are prefixed with `/api`.

| Method | Path | Auth | What it does |
|---|---|---|---|
| `POST` | `/auth/login` | None | Exchange email + password for a JWT token |
| `GET` | `/auth/me` | JWT | Return the logged-in user's info |
| `GET` | `/projects` | JWT | List all configured projects |
| `POST` | `/projects` | JWT | Create a new project |
| `GET` | `/projects/{id}` | JWT | Get a single project's configuration |
| `PUT` | `/projects/{id}` | JWT | Update project configuration |
| `DELETE` | `/projects/{id}` | JWT | Delete a project and all its results |
| `GET` | `/projects/{id}/results` | JWT | List test results (filterable by invoice, status, carrier) |
| `GET` | `/projects/{id}/carriers` | JWT | List distinct carrier names seen in results |
| `GET` | `/results/{result_id}` | JWT | Get one test result in full detail |
| `POST` | `/projects/{id}/run-test` | JWT | Trigger a test run; returns `job_id` |
| `GET` | `/jobs/{job_id}/status` | JWT | Poll job progress and get the final result |
| `POST` | `/intake` | API Key | Webhook — Lambda pushes invoice data here |
| `GET` | `/intake/health` | None | Liveness check for the tunnel |

---

### The AI Agent Pipeline

The system has three AI-capable agents. Two use a real LLM; one is purely rule-based.

---

#### Agent 1 — Log Analyzer

**File:** `agents/log_analyzer.py`

**Job:** Read the Lambda's CloudWatch logs and identify every invoice that was processed in the last N hours.

**How it works — three-tier approach:**

```
Priority 1 — Rule-based direct parser (fastest, no LLM cost)
  ↓ reads CloudWatch log messages as raw strings
  ↓ detects JSON blocks that look like invoice data
     (must have ≥ 2 of: invoice_number, total_invoice_value,
      bill_of_lading_number, currency, payment_terms, etc.)
  ↓ filters out transaction-tracking JSON (has transaction_id + step_key)
  ↓ extracts HTTP status code from surrounding plain log lines
  ↓ collects errors, warnings, execution time, cold_start
  → returns list of invoice blocks

Priority 2 — LLM fallback (if rule-based finds nothing)
  ↓ Strands AI agent with 4 CloudWatch tools
  ↓ Claude reads the raw logs and reasons about which blocks are invoices
  → returns structured JSON array

Priority 3 — Mock data (if CloudWatch is unreachable)
  → returns hardcoded demo invoice for local development
```

**Output shape (one item per invoice found):**

```json
{
  "invoice_number": "INV-0012345",
  "payload": { "invoice_date": "20-Feb-2026", "total_invoice_value": 2435, ... },
  "api_status": 200,
  "prompt_text": "...(the Lambda's own LLM prompt, captured from logs)...",
  "errors": [],
  "warnings": [],
  "execution_duration_ms": 3240,
  "cold_start": false
}
```

---

#### Agent 2 — Input Collector

**File:** `agents/input_collector.py`

**Job:** Download the reference files that tell us what the invoice fields _should_ look like.

**No AI is used here** — it is purely file I/O and parsing.

```
For each S3 file slot configured on the project:
  ├─ Excel file (.xlsx / .xls)?
  │     Open the sheet named after the vendor (e.g. "MXNG")
  │       → extract field mapping rules (field name, expected logic, remarks)
  │     Open the "Charge Map" sheet
  │       → extract charge code ↔ vendor charge name lookup
  │
  └─ Other file (plain text, CSV, prompt template)?
        Return content as-is

Also: download the invoice PDF from S3
  → convert to Markdown using PyMuPDF
  → this becomes the "ground truth" for the AI validator
```

**Excel workbook structure expected:**

```
Workbook (one file per project in S3):
  ├── Sheet "MXNG"   ← field mapping for vendor ref id MXNG
  ├── Sheet "CLIM"   ← field mapping for vendor ref id CLIM
  ├── Sheet "MSGR"   ← field mapping for vendor ref id MSGR
  └── Sheet "Charge Map"
        Row 0:  MXNG  |  CLIM  |  MSGR  ...  (vendor IDs as column headers)
        Row 1:  company name row (skipped)
        Row 2+: charge_code | master_name | vendor_name_for_each_vendor
```

---

#### Agent 3 — Payload Validator

**File:** `agents/payload_validator.py`

**Job:** Compare the extracted invoice payload against all available reference data and produce a score + suggestions.

**This is the main AI agent.** Claude receives everything collected by the previous two agents and reasons about each field.

**Validation sources Claude uses (in priority order):**

```
1. Invoice PDF (highest priority — actual source of truth)
   "The PDF says total_invoice_value = 2435. Payload says 2435 → correct."

2. Excel Field Mapping Sheet
   "Vendor MXNG sheet says invoice_number must be present. Payload has it → correct."

3. Excel Charge Map Sheet
   "Vendor MXNG charge 'Freight Charge' maps to code 400. Payload has '400' → correct."

4. Embedded Prompt Rules (lowest priority)
   "The Lambda's own prompt text has a charge table. Cross-check against that too."
```

**Scoring formula:**

```
Score = (charge_fields_score × 25%)
      + (address_fields_score × 25%)
      + (date_fields_score    × 25%)
      + (amount_fields_score  × 25%)

Each category score = (correct fields in category) / (total fields in category) × weight

Thresholds:
  ≥ 85  →  passed   (green)
  ≥ 60  →  warning  (amber)
  < 60  →  failed   (red)

Special rule: If ANY mandatory field is missing → status forced to "failed"
regardless of the numeric score.
```

**Claude's two tools:**
- `compare_field_values(field_name, expected, actual)` → `"correct" | "wrong" | "missing"`
- `calculate_weighted_score(validations_json, weights_json)` → `{ overall_score, status }`

**Fallback:** If the Bedrock call fails, a rule-based fallback checks only that each mandatory field is present (no expected-value comparison).

---

## 6. Frontend Deep Dive

The frontend is a React 18 SPA (Single-Page Application) built with Vite and styled with Tailwind CSS.

### Pages and What They Do

```
/login
  Email + password form. On success, stores JWT and user info in localStorage.
  All other routes redirect here if no token is found.

/ (Dashboard)
  Overview cards: total projects, pass rates, recent activity.
  Each ProjectCard shows: project name, last score, last tested time, status badge.

/projects
  Full list of all projects with create and delete actions.

/project/:id/configure
  Multi-step wizard to set up a project:
    Step 1 — Basic info (name, CloudWatch log group, time window)
    Step 2 — File slots (link S3 buckets and keys for Excel, PDF, prompt files)
    Step 3 — Scoring (weight percentages, mandatory fields)
    Step 4 — Notifications

/project/:id/results
  Table of all test results for a project.
  Filterable by: invoice number substring, status (passed/warning/failed), carrier name.
  Clicking a row opens a detail panel with:
    • Field-by-field validation table
    • Log summary (errors, warnings, duration)
    • Prompt improvement suggestions
    • Raw payload JSON viewer

/settings
  App-level settings.
```

### Component Map

```
src/
├── App.jsx                       ← Route definitions + auth guard
├── main.jsx                      ← React DOM entry point
├── index.css                     ← Global Tailwind imports
│
├── api/
│   └── client.js                 ← All fetch calls to the backend API
│
├── hooks/
│   ├── useProjects.js            ← Loads project list / single project
│   └── useResults.js             ← Loads results with filter support
│
├── pages/
│   ├── Login.jsx
│   ├── Dashboard.jsx
│   ├── Projects.jsx
│   ├── Configure.jsx
│   ├── Results.jsx
│   └── Settings.jsx
│
└── components/
    ├── layout/
    │   ├── Sidebar.jsx           ← Fixed left navigation bar
    │   └── TopBar.jsx            ← Top bar with page title
    ├── configure/
    │   ├── FileSlotRow.jsx       ← One row in the file-slot config table
    │   ├── StepIndicator.jsx     ← Numbered step progress bar
    │   └── TagInput.jsx          ← Chip-style input for lists (e.g. mandatory fields)
    ├── dashboard/
    │   └── ProjectCard.jsx       ← Project summary card
    └── results/
        ├── RunTestModal.jsx      ← "Run Test" dialog — shows live step progress
        ├── ScoreBadge.jsx        ← Color-coded badge: passed / warning / failed
        ├── JsonViewer.jsx        ← Collapsible tree view for raw JSON
        └── Toggle.jsx            ← On/off toggle switch
```

### How the Frontend Talks to the Backend

`src/api/client.js` is a thin wrapper around the browser's `fetch` API. It:
- Reads the JWT from `localStorage` and adds it as `Authorization: Bearer <token>` to every request.
- Automatically redirects to `/login` if the backend returns `401 Unauthorized`.
- Throws a meaningful error if any request fails.

---

## 7. Database Design

The system uses **MongoDB** with three collections.

### Collection: `projects`

Stores one document per project configuration.

| Field | Type | Description |
|---|---|---|
| `project_id` | string | Unique ID (e.g. `"ge-freight"`) |
| `project_name` | string | Human-readable name |
| `cloudwatch_log_group` | string | AWS log group path (e.g. `/aws/lambda/invoice-processor-ge`) |
| `log_window_hours` | int | How many hours back to search logs (default: 24) |
| `file_slots` | array | S3 bucket + key pairs for Excel and PDF reference files |
| `scoring_weights` | object | `{ charge_fields, address_fields, date_fields, amount_fields }` — must total 100 |
| `mandatory_fields` | array | Field names that force a "failed" result if absent |
| `status` | string | `incomplete` / `configured` / `never_tested` |
| `last_tested` | ISO 8601 | Timestamp of most recent test run |
| `last_score` | float | Score from most recent test run (0–100) |

### Collection: `results`

Stores one document per test result (one per invoice per run).

| Field | Type | Description |
|---|---|---|
| `result_id` | string | Unique ID (e.g. `"result-ge-freight-a1b2c3d4"`) |
| `project_id` | string | Links back to the project |
| `vendor_name` | string | Carrier name extracted from the payload |
| `invoice_number` | string | Invoice number extracted from the payload |
| `timestamp` | ISO 8601 | When the validation ran |
| `overall_score` | float | 0.0 – 100.0 |
| `status` | string | `passed` / `warning` / `failed` |
| `api_status` | int | HTTP status the Lambda's API call returned (e.g. 200, 400) |
| `field_validations` | array | Per-field results — see below |
| `prompt_suggestions` | array | AI-generated suggestions for improving the Lambda prompt |
| `mandatory_fields_result` | object | `{ total, passed, failed, failed_fields[] }` |
| `log_summary` | object | Errors, warnings, execution time, cold start flag |
| `raw_payload` | object | The full invoice payload JSON, stored as-is |
| `source` | string | `"lambda_push"` if created via the intake webhook |

**Each `field_validations` item:**

| Field | Values | Meaning |
|---|---|---|
| `field_name` | string | e.g. `"invoice_number"`, `"total_invoice_value"` |
| `expected_value` | string / null | What the reference files say it should be |
| `actual_value` | string / null | What the Lambda extracted |
| `status` | `correct` / `wrong` / `missing` | Validation outcome |
| `source_used` | string | Where the expected value came from |
| `is_mandatory` | bool | Whether this field is on the mandatory list |

### Collection: `jobs`

Tracks in-flight and recently completed test runs. **Auto-deleted after 1 hour** (MongoDB TTL index).

| Field | Type | Description |
|---|---|---|
| `job_id` | string | e.g. `"job-a1b2c3d4"` or `"intake-a1b2c3d4"` |
| `project_id` | string | Which project this job belongs to |
| `status` | string | `running` / `complete` / `failed` |
| `steps` | array | 5 named steps, each with `pending` / `running` / `complete` status |
| `result_id` | string | Set when complete — links to the results collection |
| `overall_score` | float | Set when complete |
| `error` | string | Set when failed — the error message |
| `created_at` | ISO 8601 | Used for TTL expiry |
| `completed_at` | ISO 8601 | Set when job finishes |

---

## 8. AWS Services Used

### Amazon S3

**Purpose:** Stores the reference files used during validation.

- **Excel workbooks** — field mapping rules and charge code tables per carrier
- **Invoice PDFs** — the actual scanned invoices (ground truth for AI validation)
- **Prompt templates** — optional text files containing additional instructions

**Required IAM permissions:** `s3:GetObject`, `s3:HeadObject`

### Amazon CloudWatch Logs

**Purpose:** The Lambda writes its processing logs here. The Log Analyzer reads these to extract invoice payloads.

Each invoice processing run in the logs contains:
- The full invoice JSON payload the Lambda built
- The HTTP status code the Pando API returned
- Any error or warning lines
- Execution duration and cold start information

**Required IAM permissions:** `logs:StartQuery`, `logs:GetQueryResults`, `logs:DescribeLogGroups`

### AWS Bedrock

**Purpose:** Runs the Claude Sonnet language model for the Log Analyzer and Payload Validator agents.

**Model used:** `us.anthropic.claude-sonnet-4-6`  
**Configurable via:** `BEDROCK_MODEL_ID` environment variable

**Required IAM permissions:** `bedrock:InvokeModel`

### Credential Configuration

AWS credentials are read from environment variables in `.env`:

```
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
```

If these are not set, boto3 falls back to the default credential chain (IAM role attached to the server, or `~/.aws/credentials`).

---

## 9. Authentication & Security

### UI Login (JWT)

- A single admin account is configured in `routers/auth.py`.
- Logging in with the correct email and password returns a **JWT token** (valid for 24 hours).
- Every API call from the browser includes this token as `Authorization: Bearer <token>`.
- The backend validates the token on every protected endpoint using a FastAPI dependency.

### Lambda Webhook (Shared Secret)

- The `/api/intake` endpoint uses a **different authentication scheme** — a shared API key.
- The Lambda must include the header `X-Intake-Key: <secret>` with every push.
- The secret is configured in the `INTAKE_API_KEY` environment variable.
- This is intentionally separate from the UI login so the Lambda doesn't need a user account.

### CORS

By default, the backend only accepts requests from `http://localhost:5173` (the Vite development server). For production, the allowed origins list in `main.py` must be updated.

---

## 10. Tech Stack at a Glance

### Backend (Python)

| What | Technology | Why |
|---|---|---|
| Web framework | **FastAPI** | Fast, async, automatic OpenAPI docs |
| AI agents | **Strands Agents SDK** | Tool-use agent framework for Claude |
| LLM | **AWS Bedrock (Claude Sonnet 4.6)** | Managed, scalable LLM inference |
| AWS SDK | **boto3** | Official Python SDK for S3, CloudWatch |
| Database | **MongoDB + PyMongo** | Flexible document store for structured results |
| Excel parsing | **openpyxl** | Read vendor mapping workbooks |
| PDF parsing | **PyMuPDF (fitz)** | Extract text from invoice PDFs |
| Auth tokens | **python-jose** | JWT encoding / decoding |
| Config | **python-dotenv** | Load `.env` files into environment |
| Server | **Uvicorn** | ASGI server for FastAPI |

### Frontend (JavaScript)

| What | Technology | Why |
|---|---|---|
| UI framework | **React 18** | Component-based UI |
| Build tool | **Vite** | Fast dev server and bundler |
| Styling | **Tailwind CSS** | Utility-first CSS |
| Routing | **React Router v6** | Client-side navigation |
| Charts | **Recharts** | Score trend visualizations |
| Icons | **Lucide React** | Clean icon set |
