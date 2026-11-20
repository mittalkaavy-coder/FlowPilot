"""Isolated schema, legacy import, owner constraints, and PG repository tests."""
import json
import sqlite3
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from core.config import BACKEND_DIR, settings
from core.db_models import (
    ApprovalEvent,
    Base,
    LEGACY_OWNER_ID,
    Plan,
    Report,
    Upload,
    User,
)
from core.pg_store import PostgresStore
from scripts.import_legacy_sqlite import import_legacy_snapshot


@pytest.fixture
def sqlite_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'foundation.db'}", future=True)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory.begin() as session:
        session.add(User(id=LEGACY_OWNER_ID, is_legacy_owner=True))
    yield engine, factory
    engine.dispose()


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return config


def test_alembic_upgrade_from_empty_database_and_repeat_is_idempotent(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'disposable-empty-schema.db'}"
    monkeypatch.setattr(settings, "database_url", database_url)
    config = _alembic_config(database_url)

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(select(func.count()).select_from(User)).scalar_one() == 1
            assert connection.execute(select(User.is_legacy_owner)).scalar_one() is True
            assert connection.execute(select(User.oidc_issuer)).scalar_one() is None
    finally:
        engine.dispose()


def test_alembic_downgrade_refuses_data_then_succeeds_when_empty(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'disposable-downgrade-safety.db'}"
    monkeypatch.setattr(settings, "database_url", database_url)
    config = _alembic_config(database_url)

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            test_user_id = uuid.uuid4()
            connection.execute(
                User.__table__.insert().values(
                    id=test_user_id, oidc_issuer="https://idp.example.test",
                    oidc_subject="downgrade-safety-test",
                )
            )
            connection.execute(
                Upload.__table__.insert().values(
                    file_id=uuid.uuid4(), owner_id=test_user_id,
                    storage_key="downgrade-safety-test/upload.csv", byte_size=1,
                )
            )

        with pytest.raises(RuntimeError, match="Refusing to drop"):
            command.downgrade(config, "base")

        with engine.connect() as connection:
            assert connection.execute(select(func.count()).select_from(User)).scalar_one() == 2

        with engine.begin() as connection:
            connection.execute(Upload.__table__.delete().where(Upload.storage_key == "downgrade-safety-test/upload.csv"))
            connection.execute(User.__table__.delete().where(User.oidc_subject == "downgrade-safety-test"))

        command.downgrade(config, "base")
        with engine.connect() as connection:
            assert "users" not in inspect(connection).get_table_names()
    finally:
        engine.dispose()


def _write_legacy_copy(tmp_path, status="completed"):
    source_root = tmp_path / "legacy-source"
    (source_root / "uploads").mkdir(parents=True)
    (source_root / "reports").mkdir()
    file_id, plan_id, report_id = (uuid.uuid4() for _ in range(3))
    upload = source_root / "uploads" / f"{file_id}.csv"
    report = source_root / "reports" / f"{report_id}.md"
    upload.write_text("Item,Value\nA,3\n", encoding="utf-8")
    report.write_text("# Legacy verified report\n", encoding="utf-8")
    plan_json = {
        "plan_id": str(plan_id),
        "goal": "Legacy test",
        "steps": [],
    }
    result = {
        "plan_id": str(plan_id),
        "report_id": str(report_id),
        "verified": True,
        "steps": [
            {"tool": "csv_inspector", "status": "success", "output": {}},
            {"tool": "csv_analyzer", "status": "success", "output": {}},
            {"tool": "report_generator", "status": "success", "output": {"report_id": str(report_id)}},
            {"tool": "artifact_verifier", "status": "success", "output": {
                "report_id": str(report_id), "verified": True,
                "integrity": {"passed": True}, "completeness": {"passed": True},
            }},
        ],
    }
    legacy_db = tmp_path / "legacy-copy.sqlite3"
    with sqlite3.connect(legacy_db) as connection:
        connection.execute(
            """CREATE TABLE plans (
                plan_id TEXT PRIMARY KEY, file_id TEXT NOT NULL, goal TEXT NOT NULL,
                plan_json TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL,
                updated_at REAL NOT NULL, result_json TEXT, error TEXT
            )"""
        )
        connection.execute(
            "INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(plan_id), str(file_id), "Legacy test", json.dumps(plan_json), status, 1_700_000_000.0, 1_700_000_001.0, json.dumps(result), None),
        )
    return legacy_db, source_root, file_id, plan_id, report_id, upload, report


@pytest.mark.parametrize("status", ["created", "approved", "executing", "completed", "failed"])
def test_legacy_import_preserves_each_supported_plan_state(sqlite_engine, tmp_path, status):
    _, factory = sqlite_engine
    legacy_db, source_root, file_id, plan_id, report_id, _, _ = _write_legacy_copy(tmp_path, status)

    counts = import_legacy_snapshot(
        source_db=legacy_db,
        source_storage_root=source_root,
        target_storage_root=tmp_path / "isolated-import-target",
        session_factory=factory,
        active_sqlite_db=tmp_path / "active-flowpilot.sqlite3",
    )

    assert counts == {"plans": 1, "uploads": 1, "reports": 1, "files_copied": 2}
    with factory() as session:
        imported_plan = session.get(Plan, plan_id)
        imported_report = session.get(Report, report_id)
        assert imported_plan.status == status
        assert imported_plan.owner_id == LEGACY_OWNER_ID
        assert imported_report.plan_id == plan_id
        assert imported_report.verified is (status == "completed")


def test_legacy_snapshot_import_is_idempotent_and_preserves_source(sqlite_engine, tmp_path):
    _, factory = sqlite_engine
    legacy_db, source_root, file_id, plan_id, report_id, upload, report = _write_legacy_copy(tmp_path)
    source_db_before = legacy_db.read_bytes()
    target_root = tmp_path / "separate-target-storage"
    args = dict(
        source_db=legacy_db,
        source_storage_root=source_root,
        target_storage_root=target_root,
        session_factory=factory,
        active_sqlite_db=tmp_path / "active-flowpilot.sqlite3",
    )

    first = import_legacy_snapshot(**args)
    second = import_legacy_snapshot(**args)

    assert first == {"plans": 1, "uploads": 1, "reports": 1, "files_copied": 2}
    assert second == {"plans": 0, "uploads": 0, "reports": 0, "files_copied": 0}
    assert legacy_db.read_bytes() == source_db_before
    assert upload.exists() and report.exists()
    assert (target_root / f"legacy/uploads/{file_id}.csv").read_bytes() == upload.read_bytes()
    assert (target_root / f"legacy/reports/{report_id}.md").read_bytes() == report.read_bytes()

    with factory() as session:
        imported_plan = session.get(Plan, plan_id)
        imported_report = session.get(Report, report_id)
        imported_upload = session.get(Upload, file_id)
        assert imported_plan.owner_id == LEGACY_OWNER_ID
        assert imported_plan.status == "completed"
        assert imported_upload.owner_id == LEGACY_OWNER_ID
        assert imported_report.owner_id == LEGACY_OWNER_ID
        assert imported_report.plan_id == plan_id
        assert imported_report.verified is True

        normal_user = User(
            id=uuid.uuid4(), oidc_issuer="https://idp.example.test", oidc_subject="user-123"
        )
        session.add(normal_user)
        session.commit()
        visible = session.scalars(select(Plan).where(Plan.owner_id == normal_user.id)).all()
        assert visible == []


def test_import_refuses_active_database_and_never_overwrites_target(sqlite_engine, tmp_path):
    _, factory = sqlite_engine
    legacy_db, source_root, file_id, _, _, upload, _ = _write_legacy_copy(tmp_path)
    with pytest.raises(ValueError, match="active SQLite"):
        import_legacy_snapshot(
            source_db=legacy_db,
            source_storage_root=source_root,
            target_storage_root=tmp_path / "target-a",
            session_factory=factory,
            active_sqlite_db=legacy_db,
        )

    target = tmp_path / "target-b"
    target_file = target / f"legacy/uploads/{file_id}.csv"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("different data", encoding="utf-8")
    with pytest.raises(FileExistsError, match="different content"):
        import_legacy_snapshot(
            source_db=legacy_db,
            source_storage_root=source_root,
            target_storage_root=target,
            session_factory=factory,
        )
    assert target_file.read_text(encoding="utf-8") == "different data"
    assert upload.exists()


def test_database_constraints_enforce_oidc_identity_owner_and_report_consistency(sqlite_engine):
    _, factory = sqlite_engine
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    file_id, plan_id, report_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with factory.begin() as session:
        session.add_all([
            User(id=user_a, oidc_issuer="https://idp.test", oidc_subject="same"),
            User(id=user_b, oidc_issuer="https://idp.test", oidc_subject="other"),
        ])
        session.flush()
        session.add(Upload(file_id=file_id, owner_id=user_a, storage_key="user-a/upload.csv", byte_size=4))
        session.flush()
    with factory.begin() as session:
        session.add(Plan(
            plan_id=plan_id, file_id=file_id, owner_id=user_a, goal="Test",
            plan_json={"plan_id": str(plan_id), "goal": "Test", "steps": []},
        ))
    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            session.add(Plan(
                plan_id=uuid.uuid4(), file_id=file_id, owner_id=user_b, goal="cross owner",
                plan_json={"goal": "cross owner", "steps": []},
            ))
    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            session.add(Report(
                report_id=report_id, plan_id=plan_id, owner_id=user_b,
                storage_key="user-b/report.md",
            ))


def test_postgres_repository_preserves_atomic_approval_and_execution_claims(sqlite_engine):
    _, factory = sqlite_engine
    owner_id, other_user_id = uuid.uuid4(), uuid.uuid4()
    file_id, plan_id = uuid.uuid4(), uuid.uuid4()
    with factory.begin() as session:
        session.add_all([
            User(id=owner_id, oidc_issuer="https://idp.test", oidc_subject="owner"),
            User(id=other_user_id, oidc_issuer="https://idp.test", oidc_subject="other"),
        ])
        session.flush()
        session.add(Upload(file_id=file_id, owner_id=owner_id, storage_key="owner/data.csv", byte_size=10))
        session.flush()
        session.add(Plan(plan_id=plan_id, file_id=file_id, owner_id=owner_id, goal="Test", plan_json={"goal": "Test", "steps": []}))
    repository = PostgresStore(factory)

    assert repository.approve_plan(plan_id=plan_id, owner_id=other_user_id, actor_user_id=other_user_id) == (False, "not_found")
    assert repository.approve_plan(plan_id=plan_id, owner_id=owner_id, actor_user_id=owner_id) == (True, "approved")
    assert repository.approve_plan(plan_id=plan_id, owner_id=owner_id, actor_user_id=owner_id) == (True, "approved")
    with factory() as session:
        events = session.scalars(select(ApprovalEvent).where(ApprovalEvent.plan_id == plan_id)).all()
        assert len(events) == 1
        assert events[0].actor_user_id == owner_id

    claimed, plan, status = repository.claim_plan_for_execution(plan_id=plan_id, owner_id=owner_id)
    assert claimed is True and status == "claimed" and plan.status == "executing"
    claimed_again, plan_again, status_again = repository.claim_plan_for_execution(plan_id=plan_id, owner_id=owner_id)
    assert claimed_again is False and status_again == "executing"
    assert plan_again.plan_id == plan_id
    assert repository.get_plan(plan_id=plan_id, owner_id=other_user_id) is None
    assert repository.get_plan(plan_id=plan_id, owner_id=owner_id).status == "executing"
