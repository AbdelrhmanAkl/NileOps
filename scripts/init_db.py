"""Initialize missing NileOps database tables safely."""

from sqlalchemy import inspect

from src.db.session import Base, engine
import src.models  # noqa: F401


def initialize_database() -> None:
    existing_before = set(inspect(engine).get_table_names())

    Base.metadata.create_all(bind=engine)

    existing_after = set(inspect(engine).get_table_names())
    created_tables = sorted(existing_after - existing_before)

    print("=== NileOps Database Initialization ===")
    print(f"Tables before: {len(existing_before)}")
    print(f"Tables after: {len(existing_after)}")

    if created_tables:
        print("\nNew tables created:")
        for table in created_tables:
            print(f"- {table}")
    else:
        print("\nNo new tables were required.")

    print("\nDatabase initialization completed.")


if __name__ == "__main__":
    initialize_database()