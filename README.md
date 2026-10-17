# NileOps — Digital Banking Operations Employee

NileOps is a digital banking operations backend designed to support KYC (Know Your Customer) document intake, validation, review workflows, and auditability.

The system combines deterministic business rules with future OCR and AI-assisted capabilities. It is designed to support human reviewers, not to make final banking decisions autonomously.

## Current Status

### Implemented

- FastAPI application with health-check endpoint.
- SQLAlchemy database models for KYC applications, documents, validation results, and audit events.
- Synthetic KYC data generation.
- Deterministic document-status validation rules.
- API endpoints for listing and filtering applications and retrieving application details.
- Automated API and validator tests.

**Latest verified test result:** 18 passed.

### Planned

- Document upload and file validation.
- OCR-based text extraction.
- AI-assisted information extraction and policy explanations.
- Human-review workflow and decision recording.
- Expanded audit-trail integration.
- Database migrations and PostgreSQL deployment.
- Operations dashboard.

Features listed as planned are not yet considered implemented.

## Technology Stack

- Python
- FastAPI
- Pydantic and Pydantic Settings
- SQLAlchemy
- SQLite for local development
- Pytest and HTTPX

PostgreSQL and Streamlit are planned for later stages.

## Project Structure

```text
NileOps/
├── src/
│   ├── api/          # FastAPI endpoints
│   ├── core/         # Application configuration
│   ├── workflows/    # Workflow orchestration
│   ├── services/     # Business logic and validation
│   ├── models/       # SQLAlchemy data models
│   ├── db/           # Database engine and sessions
│   ├── ocr/           # Planned document text extraction
│   ├── ai/            # Planned AI and retrieval components
│   └── audit/         # Audit-related components
├── tests/             # Automated tests
├── scripts/           # Development and seed scripts
├── data/              # Local database and data files
├── storage/           # Local document storage
├── docs/              # Project documentation
├── .env.example       # Example configuration
├── .gitignore
├── requirements.txt
└── README.md
```

## Prerequisites

- Git
- Python 3.13 or a compatible Python version supported by the dependencies
- PowerShell on Windows

## Setup

Run the following commands from the project root.

### 1. Clone the repository

```powershell
git clone https://github.com/AbdelrhmanAkl/NileOps.git
cd NileOps
```

### 2. Create a virtual environment

```powershell
python -m venv .venv
```

### 3. Install dependencies

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If PowerShell blocks virtual-environment activation, use the Python executable inside `.venv` directly as shown above.

### 4. Configure environment variables

Create a local `.env` file if one does not already exist:

```powershell
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
}
```

Review the configuration before running the application. Never commit `.env`, credentials, or API keys.

## Initialize the Development Database

NileOps uses a local SQLite database. Each developer should have their own local database.

From the project root, run:

```powershell
.\.venv\Scripts\python.exe -m scripts.seed_kyc_data
```

This creates the synthetic KYC applications and their initial document and audit records.

Then generate or refresh the document-level validation results:

```powershell
.\.venv\Scripts\python.exe -m src.services.kyc_validator
```

The current development dataset contains 100 synthetic applications and 300 document records. The validator is expected to produce 300 document-level findings.

Both commands should complete successfully before proceeding. Do not use real customer records or real banking documents.

## Run the API

Start the development server:

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.api.main:app --reload
```

Available local URLs:

- Health check: `http://127.0.0.1:8000/health`
- Interactive API documentation: `http://127.0.0.1:8000/docs`

Stop the server with `Ctrl+C`.

## Current API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Check API and database connectivity |
| GET | `/api/v1/kyc/applications` | List applications, with search, status filtering, and pagination |
| GET | `/api/v1/kyc/applications/{application_id}` | Retrieve application details, documents, validation results, and audit events |

Use `/docs` to inspect the actual request parameters and response schemas.

## Run the Tests

From the project root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Run the focused API and validator tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api.py tests/test_kyc_validator.py -q
```

Tests should pass before submitting code changes.

## Data and Privacy

- Development data must be synthetic or explicitly authorized for the intended use.
- The current seed script uses synthetic names, synthetic application identifiers, and fictional document scenarios.
- The database is generated locally and is excluded from Git.
- Never commit real customer information, national ID numbers, bank statements, uploaded documents, credentials, or other sensitive records.
- An uploaded document is not automatically verified.
- A preliminary validation result is not a final banking approval or rejection.

## Team Development Guidelines

1. Pull the latest approved changes before starting a task.
2. Use a separate feature branch for substantial changes.
3. Agree on API contracts before integrating modules.
4. Keep business rules deterministic and separate from AI-generated suggestions.
5. Add tests for new rules, endpoints, and important edge cases.
6. Avoid modifying another developer's work without coordination.
7. Never commit local databases, secrets, or private documents.
8. Run the test suite before requesting review.

## Important Limitations

NileOps is a development and portfolio project, not a certified banking system. Production use would require additional security controls, authentication and authorization, data retention policies, robust audit controls, migrations, operational monitoring, and appropriate human oversight.

AI and OCR features must remain assistive. Consequential banking decisions must follow approved business policies and the required human-review process.