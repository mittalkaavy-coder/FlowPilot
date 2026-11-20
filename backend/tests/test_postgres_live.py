import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import delete, func, select

from core.database import create_postgres_engine, create_session_factory
from core.db_models import ApprovalEvent, Plan, Upload, User
from core.pg_store import PostgresStore


@pytest.fixture
def postgres_store():
    database_url = os.environ.get("FLOWPILOT_DATABASE_URL")

    if not database_url:
        pytest.skip("FLOWPILOT_DATABASE_URL is not configured")

    engine = create_postgres_engine(database_url)
    factory = create_session_factory(engine)

    yield factory

    engine.dispose()


def test_live_postgres_ownership_approval_and_concurrent_claims(postgres_store):
    factory = postgres_store
    repository = PostgresStore(factory)

    owner_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    plan_id = uuid.uuid4()

    # Create two users, one upload, and one plan.
    with factory.begin() as session:
        session.add_all([
            User(
                id=owner_id,
                oidc_issuer="https://flowpilot-test.example",
                oidc_subject=str(owner_id),
            ),
            User(
                id=other_user_id,
                oidc_issuer="https://flowpilot-test.example",
                oidc_subject=str(other_user_id),
            ),
        ])

        session.flush()

        session.add(Upload(
            file_id=file_id,
            owner_id=owner_id,
            storage_key=f"phase1b-test/{file_id}/sample.csv",
            original_filename="sample.csv",
            byte_size=20,
        ))

        session.flush()

        session.add(Plan(
            plan_id=plan_id,
            file_id=file_id,
            owner_id=owner_id,
            goal="Live PostgreSQL integration test",
            plan_json={
                "plan_id": str(plan_id),
                "goal": "Live PostgreSQL integration test",
                "steps": [],
            },
        ))

    try:
        # 1. Verify owner isolation.
        assert repository.get_plan(
            plan_id=plan_id,
            owner_id=other_user_id,
        ) is None

        wrong_owner_result = repository.approve_plan(
            plan_id=plan_id,
            owner_id=other_user_id,
            actor_user_id=other_user_id,
        )

        assert wrong_owner_result == (False, "not_found")

        # 2. Verify approval and idempotency.
        first_approval = repository.approve_plan(
            plan_id=plan_id,
            owner_id=owner_id,
            actor_user_id=owner_id,
        )

        second_approval = repository.approve_plan(
            plan_id=plan_id,
            owner_id=owner_id,
            actor_user_id=owner_id,
        )

        assert first_approval == (True, "approved")
        assert second_approval == (True, "approved")

        with factory() as session:
            event_count = session.scalar(
                select(func.count())
                .select_from(ApprovalEvent)
                .where(ApprovalEvent.plan_id == plan_id)
            )

        assert event_count == 1

        # 3. Race two independent workers to claim the same plan.
        barrier = Barrier(2)

        def attempt_claim():
            barrier.wait(timeout=10)

            claimed, _, status = repository.claim_plan_for_execution(
                plan_id=plan_id,
                owner_id=owner_id,
            )

            return claimed, status

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(attempt_claim),
                executor.submit(attempt_claim),
            ]

            results = [future.result(timeout=30) for future in futures]

        successful_claims = sum(
            1 for claimed, _ in results if claimed
        )

        assert successful_claims == 1, results

        # 4. Verify final database state.
        owner_plan = repository.get_plan(
            plan_id=plan_id,
            owner_id=owner_id,
        )

        assert owner_plan is not None
        assert owner_plan.status == "executing"

        assert repository.get_plan(
            plan_id=plan_id,
            owner_id=other_user_id,
        ) is None

        print("\nLive PostgreSQL checks passed:")
        print("- Owner isolation")
        print("- Unauthorized approval rejected")
        print("- Approval recorded exactly once")
        print("- Concurrent execution claim succeeded exactly once")
        print("- Final plan status is executing")

    finally:
        # Delete only records created by this test.
        with factory.begin() as session:
            session.execute(
                delete(ApprovalEvent).where(
                    ApprovalEvent.plan_id == plan_id
                )
            )
            session.execute(
                delete(Plan).where(Plan.plan_id == plan_id)
            )
            session.execute(
                delete(Upload).where(Upload.file_id == file_id)
            )
            session.execute(
                delete(User).where(
                    User.id.in_([owner_id, other_user_id])
                )
            )
