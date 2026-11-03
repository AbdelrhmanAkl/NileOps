# NileOps — Digital Banking Operations Employee

NileOps is a portfolio-grade digital banking operations backend focused on Know Your Customer (KYC) onboarding, deterministic validation, human review, authentication, authorization, and auditability.

The project combines explicit business rules with a controlled review workflow. Future OCR and AI-assisted capabilities are planned to support document processing and policy explanations without replacing human decision-making.

> **Project status:** Active development. NileOps is a development and portfolio project, not a certified banking system.

## Table of Contents

- [Current Status](#current-status)
- [Key Features](#key-features)
- [Technology Stack](#technology-stack)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Initialize the Development Database](#initialize-the-development-database)
- [Create an Application User](#create-an-application-user)
- [Run the API](#run-the-api)
- [Authentication and Authorization](#authentication-and-authorization)
- [API Endpoints](#api-endpoints)
- [Human Review Workflow](#human-review-workflow)
- [Run the Tests](#run-the-tests)
- [Security and Data Privacy](#security-and-data-privacy)
- [Roadmap](#roadmap)
- [Limitations](#limitations)
- [Team Development Guidelines](#team-development-guidelines)

## Current Status

### Implemented

- FastAPI application with a database connectivity health check.
- SQLAlchemy models for KYC applications, documents, validation results, audit events, and application users.
- Synthetic KYC data generation.
- Deterministic document-status validation.
- Application listing, filtering, pagination, and detail endpoints.
- Password hashing using Argon2 through `pwdlib`.
- JWT access-token creation and validation.
- Database-backed authentication and role-based access control.
- Local command-line user provisioning.
- Human review decisions with validated state transitions.
- Server-controlled reviewer identity.
- Audit events for human review actions.
- Approval checks requiring all three designated document types to have a valid status.
- Automated API, validation, authentication, authorization, and review-workflow tests.

**Latest verified test result:** 30 passed, with 1 dependency deprecation warning.

This result reflects the most recent local test run; future changes must be tested again.

### Planned

- Secure document upload, file validation, and storage lifecycle controls.
- OCR-based text extraction.
- AI-assisted information extraction and policy explanations.
- Retrieval-augmented generation (RAG) for approved policies and procedures.
- Database migrations.
- PostgreSQL deployment.
- Operations dashboard.
- Production deployment controls, monitoring, and expanded security testing.

Planned features are not represented as implemented functionality.

## Key Features

### KYC Application Management

- List and search synthetic KYC applications.
- Filter applications by status.
- Paginate results.
- Retrieve application details, document metadata, validation findings, and audit events.
- Return only the last four digits of the national ID field exposed by the application detail schema.

### Authentication

- Passwords are stored as password hashes rather than plaintext.
- JWT access tokens are issued after successful authentication.
- Tokens have a configurable expiration time.
- Inactive users cannot authenticate successfully through protected routes.
- Authentication errors do not intentionally disclose whether a username exists.

### Role-Based Authorization

| Role | Intended access |
|---|---|
| `viewer` | Read KYC application data |
| `reviewer` | Read application data and record human review decisions |
| `admin` | Read application data and record human review decisions |

The current API enforces these permissions. Additional administrative capabilities, such as user management through an authenticated admin endpoint, have not yet been implemented.

### Human Review and Auditability

- Review decisions are validated against permitted state transitions.
- Approval requires all three designated document types to be present and marked valid.
- Reviewer identity is obtained from the authenticated user record.
- Review actions record the previous and resulting application states.
- Review changes and their audit events are committed together in the same database transaction.

## Technology Stack

- **Language:** Python
- **API:** FastAPI
- **Validation and configuration:** Pydantic, Pydantic Settings
- **ORM:** SQLAlchemy
- **Local database:** SQLite
- **Authentication:** PyJWT
- **Password hashing:** `pwdlib` with Argon2
- **Testing:** Pytest, FastAPI TestClient, HTTPX

PostgreSQL and Streamlit are planned for later stages.

## Architecture

The current backend follows a modular structure:

1. **API layer:** Exposes health, authentication, KYC application, and review endpoints.
2. **Core layer:** Handles application settings, token validation, and access-control dependencies.
3. **Database layer:** Configures SQLAlchemy engines, sessions, and the declarative model base.
4. **Model layer:** Defines application users and KYC-related database entities.
5. **Validation layer:** Applies deterministic document validation rules.
6. **Review workflow:** Enforces permitted status transitions and required-document checks.
7. **Audit trail:** Records human review actions and application state changes.

The intended processing flow is:

`Application Intake → Document Metadata → Deterministic Validation → Human Review → Audit Trail`

OCR, AI-assisted processing, and retrieval-based policy support are planned extensions, not prerequisites for the current workflow.

## Project Structure

```text
NileOps/
├── src/
│   ├── api/
│   │   ├── auth.py
│   │   └── main.py
│   ├── core/
│   │   ├── auth.py
│   │   ├── config.py
│   │   └── security.py
│   ├── workflows/
│   ├── services/
│   ├── models/
│   │   ├── kyc.py
│   │   └── user.py
│   ├── db/
│   │   └── session.py
│   ├── ocr/
│   ├── ai/
│   └── audit/
├── tests/
├── scripts/
│   └── create_user.py
├── data/
├── storage/
├── docs/
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

Some directories are reserved for future components and may not yet contain implemented modules.

## Prerequisites

- Git
- Python 3.13 or a compatible Python version supported by the dependencies
- PowerShell on Windows

## Installation

Run the commands from the project root.

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

Using the virtual environment's Python executable directly avoids PowerShell activation-policy issues.

## Configuration

Create a local `.env` file if it does not already exist:

```powershell
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
}
```

Configure `JWT_SECRET_KEY` with a cryptographically random secret of at least 32 characters. Do not use the placeholder from `.env.example` as a production secret.

For example, generate a candidate secret locally without printing it to the console:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; from pathlib import Path; p=Path('.env'); s=p.read_text(encoding='utf-8'); s=s.replace('REPLACE_WITH_A_RANDOM_SECRET_OF_AT_LEAST_32_CHARACTERS', secrets.token_urlsafe(48)); p.write_text(s, encoding='utf-8')"
```

Run this command only when `.env` still contains the placeholder. It does not replace an existing placeholder if it has already been changed.

Important configuration variables:

| Variable | Purpose |
|---|---|
| `APP_NAME` | Application display name |
| `APP_ENV` | Environment: `development`, `testing`, or `production` |
| `APP_DEBUG` | Debug setting; must be false in production |
| `DATABASE_URL` | SQLAlchemy database connection URL |
| `UPLOAD_DIR` | Configured upload directory |
| `MAX_UPLOAD_SIZE_MB` | Configured maximum upload size |
| `JWT_SECRET_KEY` | Secret used to sign and verify access tokens |
| `JWT_ALGORITHM` | JWT algorithm; currently `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access-token lifetime, from 1 to 60 minutes |

The current settings module validates the JWT secret and rejects production configuration with debug mode enabled.

Never commit `.env`, credentials, tokens, or real customer information.

## Initialize the Development Database

NileOps uses SQLite for local development. Each developer should use a separate local database.

Run the synthetic seed script:

```powershell
.\.venv\Scripts\python.exe -m scripts.seed_kyc_data
```

Then generate or refresh the deterministic validation findings:

```powershell
.\.venv\Scripts\python.exe -m src.services.kyc_validator
```

The existing seed workflow is documented to create 100 synthetic applications and 300 document records. Confirm the actual output of both commands before relying on those counts.

Do not use real customer records, genuine identity documents, or production banking data.

## Create an Application User

After configuring `.env` and initializing the development database, create a local user interactively:

```powershell
.\.venv\Scripts\python.exe -m scripts.create_user
```

The script prompts for a username, role, password, and password confirmation. Password input is hidden.

Supported roles:

- `viewer`
- `reviewer`
- `admin`

The script creates the users table if necessary and does not intentionally recreate the existing KYC tables.

Use a strong, unique password. User provisioning is currently a local command-line operation rather than a public registration endpoint.

## Run the API

Start the development server:

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.api.main:app --reload
```

Local endpoints:

- Health check: `http://127.0.0.1:8000/health`
- Interactive API documentation: `http://127.0.0.1:8000/docs`

Stop the development server with `Ctrl+C`.

## Authentication and Authorization

The token endpoint uses OAuth2 password-form fields.

Request a token through `/docs` or send a form-encoded request to:

```text
POST /api/v1/auth/token
```

The form fields are:

- `username`
- `password`

A successful response includes:

```json
{
  "access_token": "<signed-access-token>",
  "token_type": "bearer",
  "expires_in": 900
}
```

The `expires_in` value is expressed in seconds and depends on the configured token lifetime.

For protected endpoints, send the token using:

```text
Authorization: Bearer <access-token>
```

The server resolves the user from the database and uses the current database role for authorization. The token's role claim is not the source of truth for the access-control decision.

## API Endpoints

| Method | Endpoint | Access | Purpose |
|---|---|---|---|
| GET | `/health` | Public | Check API and database connectivity |
| POST | `/api/v1/auth/token` | Public login endpoint | Authenticate a user and issue an access token |
| GET | `/api/v1/kyc/applications` | Authenticated user | List, search, filter, and paginate applications |
| GET | `/api/v1/kyc/applications/{application_id}` | Authenticated user | Retrieve application details and related records |
| POST | `/api/v1/kyc/applications/{application_id}/review` | `reviewer`, `admin` | Record a human review decision |

Use `/docs` to inspect the request parameters, response schemas, and available operations.

## Human Review Workflow

The review endpoint accepts one of these decisions:

- `IN_REVIEW`
- `NEEDS_INFORMATION`
- `APPROVED`
- `REJECTED`

The API maps these decisions to the corresponding application statuses and rejects transitions that are not permitted.

Approval requires all three designated document types to exist and have the status `مستوفٍ مبدئيًا`:

- `بطاقة الرقم القومي`
- `إثبات العنوان`
- `استمارة بيانات العميل`

This is a development workflow rule. A valid document status is not, by itself, proof that an identity document is authentic or that a banking application meets every applicable legal or regulatory requirement.

The reviewer name is derived from the authenticated account. Clients cannot supply an alternative reviewer identity in the review payload.

## Run the Tests

Run the complete test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Run focused API and validator tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api.py tests/test_kyc_validator.py -q
```

The latest verified full-suite result is **30 passed**. The test suite currently reports a deprecation warning involving Starlette's test client and HTTPX; dependency compatibility should be reviewed before upgrading or replacing test dependencies.

Run tests again after any code or dependency change.

## Security and Data Privacy

- Use synthetic or explicitly authorized data only.
- Store passwords using a suitable password-hashing algorithm; never store plaintext passwords.
- Keep JWT signing secrets out of source control.
- Do not commit `.env`, local databases, uploaded documents, or sensitive records.
- Enforce authentication and role checks on protected API routes.
- Treat document status and automated validation findings as preliminary signals, not proof of authenticity.
- Keep human review and consequential decisions under authorized human control.
- Review token expiry, account lifecycle, audit integrity, rate limiting, secure transport, and operational monitoring before any production deployment.

The current implementation provides foundational security controls but has not been certified or independently audited for production banking use.

## Roadmap

### Security and Reliability

- Expand authentication and authorization edge-case tests.
- Review dependency compatibility and pin validated versions.
- Introduce database migrations.
- Strengthen audit controls and operational logging.
- Add deployment and configuration validation.

### Document Processing

- Secure document upload and storage.
- File type, size, and content validation.
- OCR-based text extraction.
- Human verification of extracted fields.

### AI Assistance

- AI-assisted extraction and policy explanations.
- Retrieval from approved policies and procedures.
- Evidence-backed recommendations with explicit uncertainty.
- Evaluation datasets and regression tests.

### Operations Experience

- Reviewer dashboard.
- Workflow visibility and audit-event exploration.
- PostgreSQL deployment.
- Monitoring and reproducible deployment procedures.

## Limitations

NileOps is a development and portfolio project. It is not a certified banking platform and should not process real customer information or make autonomous consequential banking decisions.

Production use would require a broader security review, formal data-protection controls, reliable migrations and backups, secure document handling, operational monitoring, audit integrity controls, incident response procedures, and appropriate legal and regulatory review.

OCR and AI components must remain assistive. Final consequential banking decisions must follow approved policies and the required human-review process.

## Team Development Guidelines

1. Pull the latest approved changes before starting a task.
2. Use feature branches for substantial changes.
3. Agree on API contracts before integrating modules.
4. Keep deterministic business rules separate from AI-generated suggestions.
5. Add tests for new endpoints, rules, and important edge cases.
6. Avoid modifying another developer's work without coordination.
7. Never commit local databases, secrets, or private documents.
8. Run the full test suite before requesting review.
9. Document implemented features separately from planned work.
