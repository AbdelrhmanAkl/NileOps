# Synthetic core-banking data (IMPLEMENTED, synthetic only)

Modelled on the structure of a typical core-banking sample (customers, accounts, transactions, loans, branches).
**All data is generated.** No real person, account, ID number or amount. Nothing is connected to a real bank.

| Table | Purpose | Notes |
|---|---|---|
| `branches` | 10 Egyptian branches (code, Arabic name, governorate) | |
| `customers` | Unique Egyptian-style names, governorate, birth date, branch | no national ID or full phone stored |
| `bank_accounts` | `account_number` (`EG0001…`), type (current/savings), currency EGP/USD, `balance`, branch | one active account per type per customer |
| `account_transactions` | ledger lines with `kind`, `amount > 0`, channel, Arabic description, `balance_after` | balance = last `balance_after`, never negative |
| `loans` | personal / auto / mortgage with annuity-based `monthly_installment` | status active/overdue/closed |

Generators: `scripts/synthetic_people.py` (names, branches), `scripts/seed_core_banking.py` (accounts, ledger, loans),
`scripts/seed_banking_data.py` (requests, complaints, cards). Run everything with `python -m scripts.reset_demo --yes`.
Tests: `tests/test_core_banking_data.py` checks ledger arithmetic, non-negative balances, unique account numbers,
the loan formula and foreign keys.

Why SQL: banking data is relational and needs constraints and transactions. SQLite for development; PostgreSQL is the
planned production database (Alembic migrations planned, not implemented).

Customers see balances, a statement and their loans at `GET /api/v1/portal/overview` and
`GET /api/v1/portal/accounts/{id}/transactions` (own accounts only; another customer's account returns 404).
