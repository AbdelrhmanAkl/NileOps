# Deployment

## Local (development)

```
python -m venv .venv && .venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env                                  # set JWT_SECRET_KEY
python -m scripts.init_db
python -m scripts.seed_kyc_data
python -m scripts.seed_banking_data
python -m scripts.load_policies
python -m scripts.create_user                           # create admin/reviewer/viewer
python -m uvicorn src.api.main:app --reload
```

For a quick demo, replace the seed/create_user lines with `python -m scripts.setup_demo` (creates `demo_viewer`, `demo_reviewer`, `demo_admin`, password `Demo-Pass-2026!`; refuses in production).

Open <http://127.0.0.1:8000/dashboard> (UI) or `/docs` (API).

## Docker

```
copy deploy\env.production.example deploy\.env.production   # fill it in
docker compose up -d --build
docker compose exec nileops python -m scripts.create_user
```

The container installs Tesseract (Arabic) and Poppler, creates the database and loads policies on start, and runs as a non-root user. Data lives in the `nileops_data` and `nileops_storage` volumes.

Put a TLS reverse proxy in front (the compose file binds to `127.0.0.1` only) and set `ALLOWED_HOSTS` to your domain.

## Production checklist

- `APP_ENV=production`, `APP_DEBUG=false`, `DOCS_ENABLED=false`, strong random `JWT_SECRET_KEY`.
- `ALLOWED_HOSTS` set; HTTPS only; rate-limit `/api/v1/auth/token` at the proxy.
- Backups: `python -m scripts.backup_db backups/` (consistent SQLite copy) plus a copy of the upload directory. Test a restore.
- Local LLM (optional): run Ollama, `ollama pull qwen2.5:7b-instruct`, set `LLM_PROVIDER=ollama`, `LLM_BASE_URL`, `LLM_MODEL`. Without it the assistant answers extractively from policy text.
- Add or change policies by editing `knowledge/policies/*.md` (bump `version`), then admin → "System status" → reload policies.
- Read `docs/security.md` for known limits.

## Upgrading to the customer portal (Phase 8)

The `users.role` CHECK constraint now allows `customer`, and two link tables were added
(`portal_customer_accounts`, `portal_kyc_links`). `create_all` cannot alter an existing
table, so an existing **development/demo** database must be recreated:

```powershell
python -m scripts.reset_demo --yes     # wipes data + uploads, reseeds synthetic demo data
```

(or delete `data/nileops.db` and run `python -m scripts.setup_demo`). `reset_demo` refuses to run
when `APP_ENV=production`. Never do this to a database holding data you need; back it up first
with `python -m scripts.backup_db`. Formal migrations (Alembic) are planned.
