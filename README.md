# NileOps

NileOps is a digital banking operations project that helps teams receive documents, validate information, review issues, and track decisions.

The goal is to make operational workflows easier to follow, test, and audit.

## Current Status

The project structure and basic Python environment are ready.
The application features are still under development.

## How the Workflow Will Work

1. Receive a request and its documents.
2. Check the files and validate the available information.
3. Extract document text using OCR when needed.
4. Apply business rules to identify missing or conflicting information.
5. Use AI to help explain findings and retrieve relevant policies.
6. Send cases that need attention to a human reviewer.
7. Record validation results, recommendations, and human decisions in an audit trail.

**Important:** OCR and AI provide information and assistance. They do not make final banking decisions automatically.

## Technology Stack

- Python
- FastAPI for the backend API
- Pydantic for data validation
- SQLAlchemy for database access
- SQLite for local development
- PostgreSQL planned for a later deployment stage
- Pytest and HTTPX for testing
- Streamlit planned for the operations dashboard

## Project Structure

```text
NileOps/
├── src/
│   ├── api/         # API endpoints
│   ├── core/        # Shared configuration and core logic
│   ├── workflows/   # Workflow steps and coordination
│   ├── services/    # Business services
│   ├── models/      # Data models and schemas
│   ├── db/          # Database setup and access
│   ├── ocr/         # Document text extraction
│   ├── ai/          # AI and retrieval components
│   └── audit/       # Audit events and decision history
├── tests/           # Automated tests
├── docs/            # Technical documentation
├── data/            # Local data; do not commit private data
├── storage/         # Local file storage
├── scripts/         # Development and maintenance scripts
├── .env.example     # Example environment configuration
├── .gitignore       # Files excluded from Git
└── requirements.txt # Python dependencies
```

## Setup

Run these commands from the project directory in PowerShell.

Create the virtual environment:

```powershell
python -m venv .venv
```

Install dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If PowerShell blocks script activation, use the Python executable inside `.venv` directly.

## Configuration

Use `.env.example` as a reference for local settings.

Create a local `.env` file when needed. Never commit API keys, passwords, or other secrets.

## Team Development Rules

- Keep modules small and responsibilities clear.
- Agree on API contracts before implementing integrations.
- Keep business rules separate from AI-generated suggestions.
- Add tests for important behavior and API changes.
- Do not overwrite another team member's work without coordination.
- Use synthetic or properly authorized data during development.
- Record important decisions and changes clearly.

## Security

NileOps is a project under development, not a certified banking system. Real customer data and consequential banking workflows require appropriate authorization, security controls, testing, and human oversight.
