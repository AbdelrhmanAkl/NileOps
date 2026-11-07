"""The synthetic core-banking data must be internally consistent, like a real ledger."""

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scripts.seed_core_banking import monthly_installment, seed_core_banking
from scripts.synthetic_people import person
from src.db.session import Base, enable_sqlite_foreign_keys
from src.models.banking import AccountTransaction, BankAccount, Customer, Loan


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        for i in range(1, 41):
            session.add(Customer(customer_ref=f"C-{i:04d}", full_name_ar="x"))
        session.commit()
        seed_core_banking(session, today=date(2026, 11, 1))
        session.commit()
        yield session


def test_names_are_unique_and_clean():
    names = [person(i)["name"] for i in range(1, 200)]
    assert len(set(names)) == len(names)
    assert all(len(n.split()) >= 3 for n in names)


def test_each_balance_equals_the_last_ledger_line_and_never_negative(db):
    for account in db.scalars(select(BankAccount).where(BankAccount.status == "active")):
        lines = db.scalars(select(AccountTransaction).where(AccountTransaction.account_id == account.id)
                           .order_by(AccountTransaction.posted_at, AccountTransaction.id)).all()
        assert lines, account.account_ref
        assert account.balance == lines[-1].balance_after
        assert all(line.balance_after >= 0 for line in lines)


def test_ledger_arithmetic_is_consistent(db):
    credits = {"deposit", "salary", "transfer_in", "interest"}
    account = db.scalars(select(BankAccount)).first()
    lines = db.scalars(select(AccountTransaction).where(AccountTransaction.account_id == account.id)
                       .order_by(AccountTransaction.posted_at, AccountTransaction.id)).all()
    running = 0
    for line in lines:
        running = running + line.amount if line.kind in credits else running - line.amount
        assert running == line.balance_after


def test_every_account_has_a_unique_number_branch_and_currency(db):
    accounts = db.scalars(select(BankAccount)).all()
    numbers = [a.account_number for a in accounts]
    assert all(numbers) and len(set(numbers)) == len(numbers)
    assert all(a.branch_id and a.currency in {"EGP", "USD"} for a in accounts)
    assert all(c.branch_id and c.governorate and c.date_of_birth for c in db.scalars(select(Customer)))


def test_loan_installment_matches_the_annuity_formula(db):
    loan = db.scalars(select(Loan)).first()
    assert loan.monthly_installment == monthly_installment(loan.principal_amount, loan.annual_rate, loan.tenure_months)
    assert loan.monthly_installment * loan.tenure_months > loan.principal_amount


def test_database_rejects_orphan_transactions(db):
    from sqlalchemy.exc import IntegrityError

    db.add(AccountTransaction(account_id=999999, posted_at=datetime(2026, 1, 1), kind="deposit", amount=1, currency="EGP",
                              channel="branch", description_ar="x", balance_after=1))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.scalar(select(func.count()).select_from(AccountTransaction)) > 0
