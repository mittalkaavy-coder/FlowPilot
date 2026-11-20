# Phase 1C Plan: PostgreSQL Runtime and Secure Multi-User Workflows

## Objective

Integrate PostgreSQL into the FlowPilot application runtime and introduce secure authentication with verified user identity and strict per-user ownership of workflow data. SQLite remains the current runtime throughout planning and implementation until a controlled cutover is explicitly completed.

This plan reflects the repository as inspected. It does not imply that authentication or PostgreSQL runtime integration is already implemented.

## Current state

### Implemented

- **SQLite runtime:** `backend/core/store.py` owns the active workflow persistence. It creates/uses a SQLite `plans` table, enables WAL, saves and retrieves plans, records results/errors, approves plans, atomically claims approved plans with `BEGIN IMMEDIATE`, and supports report lookup. API routes import these functions directly.
- **Workflow lifecycle:** plans transition through `created`, `approved`, `executing`, and terminal `completed` or `failed` states. Approval is explicit and execution is replay protected. Recovery and execution endpoints use this SQLite store.
- **File safety:** CSV validation, UUID checks, canonical path checks, upload size limits, report verification, and stale-file cleanup exist. These protect filesystem paths but do not establish a user's identity or ownership.
- **PostgreSQL foundation:** SQLAlchemy, Alembic, and psycopg dependencies are present. `FLOWPILOT_DATABASE_URL` is parsed as optional configuration; `core/database.py` can create a PostgreSQL engine/session factory; SQLAlchemy models describe users, sessions, uploads, plans, approval events, and reports; and migration `0001_multi_user_foundation` creates that schema and a reserved legacy owner.
- **Partial PostgreSQL repository:** `core/pg_store.py` implements owner-filtered plan/report reads, upload/plan persistence, approval audit recording, and conditional execution claiming. The repository is not wired into FastAPI and is not a shared interface with SQLite.
- **Migration/import groundwork:** the Alembic environment and first schema migration exist. `scripts/import_legacy_sqlite.py` imports a separate SQLite snapshot and artifacts into an already-migrated target under the reserved legacy owner. It includes collision checks and idempotency behavior.
- **Foundation tests:** SQLite-backed tests cover migration behavior, schema constraints, repository transition semantics, and import behavior. Environment-gated live PostgreSQL tests cover selected ownership constraints, approval, independent-connection execution claims, PostgreSQL types, rollback/recovery, and legacy import. They skip without a configured database URL.

### Missing or incomplete

- **Authentication:** there are user/session schema rows but no login, logout, session issuance/validation, CSRF enforcement, identity-provider integration, or verified-identity dependency in the request path. No route derives a trusted user ID.
- **Ownership in the active application:** active upload, plan, recovery, approval, execution, report lookup, and download routes are unauthenticated and contain no owner filter. SQLite plans have no owner column; uploaded files and reports are filesystem artifacts addressed by UUID. UUID unpredictability is not authorization.
- **Shared repository contract:** route code calls standalone SQLite functions directly. No protocol/interface is defined, and SQLite has no ownership-aware methods matching PostgreSQL.
- **Postgres lifecycle parity:** `PostgresStore` has no equivalent `get_workflows_for_report`, no complete conversion to the existing `StoredPlan`/`WorkflowPlan`/`ExecuteResponse` contract, and no integrated upload/report artifact lifecycle. Its callers and app startup/shutdown do not select or manage it. PostgreSQL row status checks and persistence exist, but parity at the application boundary is unfinished.
- **Startup and shutdown integration:** FastAPI lifespan only manages optional cleanup. It does not construct, validate, expose, or dispose a database engine/store, and runtime selection remains SQLite regardless of `FLOWPILOT_DATABASE_URL`.
- **Production authentication policy:** trusted issuer/audience validation, verified email policy, session security/rotation/revocation, cookie settings, CSRF/origin policy, and identity lifecycle are not implemented. The schema alone is not authentication.
- **Cutover readiness:** importer groundwork exists, but no operational runbook establishes a frozen source snapshot, verification gates, traffic switch, monitoring period, or tested rollback to SQLite.

## Milestones

### 1. Authentication and verified user identity

Choose and document the supported identity provider and trusted issuer/audience configuration. Implement server-side identity verification and map only validated issuer/subject pairs to application users. Define verified-account requirements explicitly; never accept a user ID, email, owner ID, or role supplied by the browser as proof of identity. Implement secure session creation, lookup, expiry, rotation/revocation, logout, cookie policy, and CSRF protection appropriate to the chosen browser flow. Provide one request dependency that returns the authenticated principal or rejects the request. Fail closed when identity/session validation is unavailable.

### 2. Repository interface shared by SQLite and PostgreSQL

Define a typed repository contract around the operations the routes need: save upload, retrieve an owned upload, save/get/list plans, approve, claim execution, persist terminal state/result/error, retrieve owned report/workflow state, and any cleanup/recovery operations. Make the authenticated owner explicit in every user-data operation. Adapt SQLite behind this contract while preserving its transaction and lifecycle guarantees; implement PostgreSQL against the same return shapes and error semantics. Avoid route-specific SQL or storage-backend branches.

### 3. Complete `PostgresStore` parity with the SQLite workflow lifecycle

Make PostgreSQL operations match the SQLite lifecycle and public domain representation, including initial plan state, recovery, explicit approval and idempotency, atomic single execution claim across processes, terminal success/failure persistence, error/result serialization, report association and verified-report lookup, and connection/session failure behavior. Preserve the invariant that only an approved plan can be claimed and only one claimant succeeds. Add missing upload/report methods and consistency checks, including storage metadata and verified artifact status. Document any intentional differences in transaction behavior.

### 4. Owner-scoped uploads, plans, reports, approvals, and downloads

Require an authenticated principal on every workflow and artifact endpoint. Persist upload ownership before allowing planning; verify that the requested upload belongs to that principal. Scope plan recovery, approval, execution, and all result/error reads and writes to the principal. Record the principal as approval actor and preserve the approval audit event. Scope report discovery and download authorization to the owner, then resolve only the database-recorded storage key through the existing path-containment protections. Return a consistent non-disclosing not-found response for another user's IDs. Ensure cleanup and background work cannot cross ownership boundaries or remove a live user's referenced artifacts.

### 5. Database dependency injection and application startup/shutdown

Add a single application-level database/store construction point with explicit configuration and dependency injection. Keep SQLite as the default/current runtime until cutover is deliberately enabled. At startup, validate backend configuration and database connectivity/migration expectations; do not silently fall back from a failed PostgreSQL configuration to SQLite. Expose the repository through request dependencies. At shutdown, stop background work and dispose PostgreSQL engine resources cleanly. Keep test app construction isolated so tests can inject SQLite, PostgreSQL, and fakes without connecting to a real service.

### 6. Migration, rollback, and data migration strategy

Maintain reviewed, versioned Alembic migrations with upgrade and downgrade behavior documented. Foundation tests exercise a populated-database downgrade guard in the SQLite test path. The PostgreSQL migration rehearsal covered downgrade and re-upgrade only on an empty disposable database; populated PostgreSQL downgrade protection remains unverified and must be tested before production use; rollback of an application release must not imply destructive schema downgrade. Use backups and forward fixes for production schema recovery.

For data migration, use a consistent, read-only SQLite snapshot and matching artifact snapshot, not the live database. The existing importer provides a starting point: it validates expected paths, copies artifacts without overwriting different bytes, preserves supported plan states, associates historical records with the reserved non-login legacy owner, and is designed to be idempotent. Define preflight inventory, hashes/counts, orphan/missing artifact handling, conflict resolution, dry-run/reporting, repeatable import, and post-import reconciliation. Never map historical anonymous data to a newly authenticated person without an explicit, audited ownership-transfer process and user authorization.

### 7. Tests for isolation, ownership, concurrency, recovery, and regressions

Add backend-contract tests that run against SQLite and PostgreSQL implementations, plus authenticated API integration tests. Cover missing/invalid/unverified identity, session expiry/revocation and CSRF, cross-user upload/plan/report/approval/download denial, owner-scoped execution and recovery, composite ownership constraints, and non-disclosing responses. Exercise concurrent approvals and execution claims using independent connections/processes, transaction rollback and connection recovery, restart/recovery of every lifecycle state, migration/import idempotency and conflicts, artifact hash/availability behavior, and cleanup safety. Retain and run existing upload, planner, tool, workflow, security, CORS, cleanup, and SQLite replay/regression coverage. Live PostgreSQL tests must be explicitly configured and isolated; routine tests must not accidentally contact a production database.

### 8. Controlled cutover and rollback strategy

Keep SQLite as the production runtime until authentication, repository parity, ownership enforcement, migration rehearsal, backups, reconciliation, and regression gates pass. Rehearse against disposable PostgreSQL using a frozen copy of representative SQLite data and artifacts. Define a cutover window, write freeze, final snapshot/import, row and artifact hash/count reconciliation, smoke tests, monitoring signals, operator decision point, and named rollback owner. Switch runtime configuration only after gates pass; retain an immutable SQLite snapshot and the prior deploy/configuration for the agreed rollback window. If rollback is needed, stop writes, preserve PostgreSQL evidence, restore the known-good SQLite runtime/snapshot, and reconcile any post-cutover writes through a documented process before reopening traffic. Do not dual-write without a separately designed consistency protocol.

## Exit criteria

- Every protected API request has a verified principal, and user data cannot be read or changed across owners.
- SQLite and PostgreSQL satisfy the same repository contract and workflow lifecycle behavior.
- FastAPI startup/shutdown owns database resources and fails safely on misconfiguration.
- Migration and legacy import have rehearsed backup, validation, conflict, and recovery procedures.
- Required isolation, concurrency, recovery, migration, and existing regression suites pass, including explicitly provisioned PostgreSQL coverage.
- A documented, rehearsed cutover and application rollback procedure has operational approval before PostgreSQL becomes the runtime.

Until those criteria and the controlled cutover are complete, SQLite remains the application runtime; the existing PostgreSQL schema, migration, and repository are groundwork only.
