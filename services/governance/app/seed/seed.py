"""Seed the governance control-plane with demo data.

Inserts (all idempotent -- safe to re-run):

* the demo tenant ``acme``;
* the five RBAC roles (viewer/analyst/operator/auditor/admin);
* the persisted permission matrix, projected from
  :data:`edis_platform.authz.rbac.ROLE_PERMISSIONS` (the code-defined matrix the
  runtime ``evaluate()`` uses) -- so the DB projection and the runtime check can
  never silently diverge;
* one ``calibration_prior`` row for ``(acme, operational_fix)`` (the pre-seeded
  static prior the Decision engine reads; ``n=0`` in the MVP).

Run as a module against a migrated DB::

    EDIS_DATABASE_URL=postgresql+asyncpg://edis:edis@localhost:5432/edis \\
        python -m app.seed.seed

Connects lazily through the platform's async sessionmaker; importing this module
opens nothing, so it is safe to import in CI without a database.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from edis_platform.authz.rbac import ROLE_PERMISSIONS
from edis_platform.db.session import get_sessionmaker
from edis_platform.logging import configure_logging, get_logger
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppRole, CalibrationPrior, Permission, Tenant

logger = get_logger("edis.governance.seed")

#: The demo tenant used throughout the EDIS demo scenario.
DEMO_TENANT_ID = "acme"
DEMO_TENANT_NAME = "Acme Corporation"

#: Human-readable role descriptions for the registry.
ROLE_DESCRIPTIONS: dict[str, str] = {
    "viewer": "Read-only access to KPIs, metrics, findings, forecasts, recommendations.",
    "analyst": "Reads all data and queries the copilot.",
    "operator": "Analyst plus acting on recommendations (accept/reject/outcome).",
    "auditor": "Reads the audit log, lineage, and explainability records.",
    "admin": "Full access, including RBAC administration.",
}

#: The pre-seeded static calibration prior for the built playbook.
DEMO_CALIBRATION = {"playbook_id": "operational_fix", "value": 0.74, "n": 0}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def seed_tenant(session: AsyncSession) -> None:
    await session.execute(
        pg_insert(Tenant)
        .values(id=DEMO_TENANT_ID, name=DEMO_TENANT_NAME, created_at=_utc_now())
        .on_conflict_do_nothing(index_elements=["id"])
    )


async def seed_roles(session: AsyncSession) -> None:
    for name in ROLE_PERMISSIONS:
        await session.execute(
            pg_insert(AppRole)
            .values(name=name, description=ROLE_DESCRIPTIONS.get(name))
            .on_conflict_do_nothing(index_elements=["name"])
        )


async def seed_permissions(session: AsyncSession) -> None:
    """Project ROLE_PERMISSIONS ("action:resource_type") into permission rows."""

    for role, granted in ROLE_PERMISSIONS.items():
        for perm in granted:
            action, _, resource_type = perm.partition(":")
            await session.execute(
                pg_insert(Permission)
                .values(role=role, action=action, resource_type=resource_type)
                .on_conflict_do_nothing(index_elements=["role", "action", "resource_type"])
            )


async def seed_calibration_prior(session: AsyncSession) -> None:
    await session.execute(
        pg_insert(CalibrationPrior)
        .values(
            tenant_id=DEMO_TENANT_ID,
            playbook_id=DEMO_CALIBRATION["playbook_id"],
            value=DEMO_CALIBRATION["value"],
            n=DEMO_CALIBRATION["n"],
            updated_at=_utc_now(),
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "playbook_id"])
    )


async def seed_all(session: AsyncSession) -> None:
    """Run every seed step in dependency order within one transaction."""

    await seed_tenant(session)
    await seed_roles(session)
    await seed_permissions(session)  # FK-free; references role names
    await seed_calibration_prior(session)  # FK -> tenant.id (seeded above)
    await session.commit()


async def main() -> None:
    configure_logging("governance-seed")
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        await seed_all(session)
    logger.info(
        "governance seed complete",
        extra={
            "tenant": DEMO_TENANT_ID,
            "roles": list(ROLE_PERMISSIONS.keys()),
            "calibration_prior": DEMO_CALIBRATION,
        },
    )


if __name__ == "__main__":
    asyncio.run(main())
