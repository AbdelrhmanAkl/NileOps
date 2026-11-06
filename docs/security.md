# Security notes

NileOps is a portfolio/MVP system on **synthetic data**. This page lists what is enforced in code, what is tested, and what a real deployment would still need.

## Enforced in code (and tested)

| Area | Control |
|---|---|
| Authentication | JWT bearer tokens (short expiry), Argon2 password hashes, `GET /api/v1/auth/me` for the UI |
| Authorization | Role checks on every endpoint (`viewer` read-only, `reviewer` decides, `admin` manages users/audit). Admin cannot change or deactivate their own account; the last active admin cannot be removed |
| Brute force | In-memory login throttle: 5 failures per username+address and 20 per address within 15 minutes → HTTP 429 + `Retry-After`; locking a real account writes a `LOGIN_LOCKED` audit event |
| Passwords | At least 12 characters, 3 character classes, not the username, not trivially common |
| Personal data | Viewer role never receives a full national ID or mobile number (last 4 only; the source line is removed). Dashboard summary contains no names or free text |
| Audit | Every human decision, upload, OCR run, policy question, user change and explanation is audited; entries hold no secrets, passwords or question text |
| HTTP | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, HSTS in production, `Cache-Control: no-store` on the API |
| Web UI | Strict CSP (`script-src 'self'`, no inline code, no external origins), data is inserted as text nodes only (never HTML), static files served from an allow-list |
| Hosts / docs | `ALLOWED_HOSTS` enables TrustedHost; `DOCS_ENABLED=false` hides `/docs` and `/openapi.json` |
| Uploads | Type/size checks, random stored names, hash de-duplication, locked after review |
| AI | Advisory only. LLM URL must be local; LLM output is validated (citations, numbers, novel terms); customer/employee free text never reaches the LLM |

## Known limits (be honest about these)

- The login throttle is **per process and in memory**: it resets on restart and is not shared between workers. Use a reverse-proxy rate limit or Redis in production.
- The UI keeps the token in `sessionStorage`. A successful XSS could read it; the CSP and text-only rendering make XSS unlikely, but production should use an `HttpOnly` cookie with CSRF protection.
- SQLite, no migrations (`create_all`). PostgreSQL and Alembic are untested here.
- OCR quality on real documents and a real Qwen model are not measured in this repository.
- No MFA, no token revocation list, no password-expiry policy.
- Uploaded files are not virus-scanned and are stored unencrypted on disk.

## Before any real use

Security review and penetration test, TLS everywhere, secrets manager, encrypted storage and backups, centralized logging/monitoring, data-protection and regulatory review.

## Production guard (enforced in code)

With `APP_ENV=production` the application refuses to start unless `ALLOWED_HOSTS` lists real host
names (not `*`), `DOCS_ENABLED=false` and `APP_DEBUG=false`. The JWT secret must be a real random value
in every environment. This is a startup check only; TLS must still be terminated by a reverse proxy.
