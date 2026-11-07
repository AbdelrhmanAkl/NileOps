"""Synthetic core-banking data: branches, balances, a consistent transaction ledger and loans.

Everything is generated (no real person, account or amount). Deterministic per customer so a reset gives
the same data. The ledger is internally consistent: every line carries `balance_after`, the account balance
is the last `balance_after`, and no account goes negative.
"""

import random
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from scripts.synthetic_people import BRANCHES, person
from src.models.banking import (
    AccountOpeningRequest,
    AccountTransaction,
    BankAccount,
    Branch,
    Customer,
    Loan,
)

LEDGER_MONTHS = 6
OPEN_REQUEST_STATUSES = ("pending", "under_review", "needs_information")
CENT = Decimal("0.01")

DESCRIPTIONS = {
    "deposit": "إيداع نقدي بالفرع",
    "salary": "إيداع مرتب",
    "withdrawal": "سحب نقدي من ماكينة ATM",
    "purchase": "عملية شراء بالبطاقة",
    "transfer_out": "تحويل إلى حساب آخر",
    "transfer_in": "تحويل وارد",
    "fee": "رسوم خدمة شهرية",
    "interest": "عائد حساب التوفير",
    "loan_installment": "قسط قرض",
}
CHANNELS = {
    "deposit": "branch", "salary": "online", "withdrawal": "ATM", "purchase": "POS", "transfer_out": "mobile",
    "transfer_in": "mobile", "fee": "system", "interest": "system", "loan_installment": "system",
}
CREDITS = {"deposit", "salary", "transfer_in", "interest"}


def money(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def monthly_installment(principal: Decimal, annual_rate: Decimal, months: int) -> Decimal:
    r = annual_rate / 12
    return money(principal * r / (1 - (1 + r) ** -months))


def ensure_branches(session) -> dict[str, Branch]:
    found = {b.code: b for b in session.scalars(select(Branch)).all()}
    for code, name, governorate in BRANCHES:
        if code not in found:
            found[code] = Branch(code=code, name_ar=name, governorate=governorate)
            session.add(found[code])
    session.flush()
    return found


def apply_profile(customer: Customer, index: int, branches: dict[str, Branch]) -> None:
    p = person(index)
    branch = branches[p["branch"][0]]
    customer.full_name_ar = p["name"]
    customer.governorate = p["governorate"]
    customer.date_of_birth = p["born"]
    customer.branch_id = branch.id
    customer.branch_name = branch.name_ar
    customer.phone_last_four = p["mobile_last_four"]


def make_loan(session, customer: Customer, rng: random.Random, today: date, serial: int) -> Loan:
    kind, (low, high), (t_low, t_high), (r_low, r_high) = rng.choice([
        ("personal", (20_000, 150_000), (12, 60), (0.17, 0.21)),
        ("auto", (150_000, 900_000), (36, 72), (0.15, 0.19)),
        ("mortgage", (500_000, 3_000_000), (120, 240), (0.09, 0.12)),
    ])
    principal = money(round(rng.randint(low, high), -3))
    months = rng.randrange(t_low, t_high + 1, 12)
    rate = Decimal(str(round(rng.uniform(r_low, r_high), 4)))
    elapsed = rng.randint(2, min(30, months - 1))
    start = today - timedelta(days=elapsed * 30)
    loan = Loan(
        loan_ref=f"LN-{serial:06d}", customer_id=customer.id, loan_type=kind, principal_amount=principal,
        annual_rate=rate, start_date=start, tenure_months=months,
        monthly_installment=monthly_installment(principal, rate, months),
        status="overdue" if serial % 12 == 0 else "active", simulation_only=True,
    )
    session.add(loan)
    session.flush()
    return loan


def build_ledger(session, account: BankAccount, rng: random.Random, today: date, loan: Loan | None) -> None:
    """Create the account's transactions and set its balance to the final balance_after."""
    savings = account.account_type == "savings"
    start_day = today - timedelta(days=LEDGER_MONTHS * 30)
    at = lambda d, hour=None: datetime.combine(d, time(hour if hour is not None else rng.randint(8, 21), rng.randint(0, 59)), tzinfo=timezone.utc)  # noqa: E731
    usd = account.currency == "USD"
    scale = Decimal("0.03") if usd else Decimal(1)

    events: list[tuple[datetime, str, Decimal]] = []
    opening = money(rng.randint(10, 250) * 1000 if savings else rng.randint(5, 60) * 1000) * scale
    events.append((at(start_day, 10), "deposit", money(opening)))
    salary = money(rng.randint(60, 450) * 100) * scale
    for m in range(LEDGER_MONTHS):
        month_start = start_day + timedelta(days=30 * m + 5)
        if savings:
            events.append((at(month_start + timedelta(days=2)), "deposit", money(rng.randint(10, 80) * 100) * scale))
            if m % 3 == 2:
                events.append((at(month_start + timedelta(days=24)), "interest", money(rng.randint(40, 600)) * scale))
            if rng.random() < 0.25:
                events.append((at(month_start + timedelta(days=15)), "withdrawal", money(rng.randint(5, 40) * 100) * scale))
            continue
        events.append((at(month_start, 9), "salary", salary))
        events.append((at(month_start + timedelta(days=1), 9), "fee", money(rng.choice([10, 15, 20])) * scale))
        for _ in range(rng.randint(7, 13)):
            kind = rng.choices(["withdrawal", "purchase", "transfer_out", "transfer_in"], weights=[30, 45, 18, 7])[0]
            amount = {
                "withdrawal": rng.randint(5, 30) * 100, "purchase": rng.randint(8, 250) * 10,
                "transfer_out": rng.randint(2, 80) * 100, "transfer_in": rng.randint(5, 60) * 100,
            }[kind]
            events.append((at(month_start + timedelta(days=rng.randint(1, 27))), kind, money(amount) * scale))
        if loan is not None and month_start >= loan.start_date:
            events.append((at(month_start + timedelta(days=3), 6), "loan_installment", loan.monthly_installment))

    events.sort(key=lambda e: e[0])
    balance = Decimal("0.00")
    for posted_at, kind, amount in events:
        if kind not in CREDITS and amount > balance:
            amount = money(balance * Decimal("0.5"))
            if amount < Decimal("1.00"):
                continue
        balance = balance + amount if kind in CREDITS else balance - amount
        session.add(AccountTransaction(
            account_id=account.id, posted_at=posted_at, kind=kind, amount=amount, currency=account.currency,
            channel=CHANNELS[kind], description_ar=DESCRIPTIONS[kind], balance_after=balance,
        ))
    account.balance = balance


def open_account(session, customer: Customer, kind: str, currency: str, rng: random.Random) -> BankAccount:
    account = BankAccount(
        account_ref=f"TMP-{customer.id}-{kind}-{currency}", customer_id=customer.id, account_type=kind,
        status="active", currency=currency, branch_id=customer.branch_id, simulation_only=True,
    )
    session.add(account)
    session.flush()
    account.account_ref = f"SIM-ACC-{account.id:06d}"
    account.account_number = f"EG0001{account.id:08d}"
    return account


def seed_customer_finances(session, customer: Customer, index: int, today: date | None = None) -> None:
    """Give one customer a current account (+ maybe savings/USD/loan) with a consistent ledger."""
    today = today or date.today()
    rng = random.Random(index * 104729)
    open_types = set(session.scalars(select(AccountOpeningRequest.account_type).where(
        AccountOpeningRequest.customer_id == customer.id,
        AccountOpeningRequest.status.in_(OPEN_REQUEST_STATUSES))).all())
    have = {(a.account_type, a.currency) for a in session.scalars(
        select(BankAccount).where(BankAccount.customer_id == customer.id, BankAccount.status == "active")).all()}
    wanted: list[tuple[str, str]] = []
    if "current" not in open_types:
        wanted.append(("current", "EGP"))
    if "savings" not in open_types and rng.random() < 0.45:
        wanted.append(("savings", "EGP"))
    if rng.random() < 0.08:
        wanted.append(("savings", "USD"))
    for kind, currency in wanted:
        if (kind, currency) not in have:
            open_account(session, customer, kind, currency, rng)
    session.flush()

    loan = make_loan(session, customer, rng, today, serial=index) if rng.random() < 0.4 else None
    for account in session.scalars(select(BankAccount).where(
            BankAccount.customer_id == customer.id, BankAccount.status == "active")).all():
        if account.account_number is None:
            account.account_number = f"EG0001{account.id:08d}"
            account.branch_id = customer.branch_id
        if account.balance == 0:
            build_ledger(session, account, rng, today, loan if account.account_type == "current" else None)


def seed_core_banking(session, today: date | None = None) -> dict[str, int]:
    """Enrich every existing customer. Safe to run once on a fresh database."""
    branches = ensure_branches(session)
    for customer in session.scalars(select(Customer).order_by(Customer.id)).all():
        apply_profile(customer, customer.id, branches)
        seed_customer_finances(session, customer, customer.id, today)
    session.flush()
    return {
        "branches": len(branches),
        "accounts": len(session.scalars(select(BankAccount)).all()),
        "transactions": len(session.scalars(select(AccountTransaction.id)).all()),
        "loans": len(session.scalars(select(Loan.id)).all()),
    }
