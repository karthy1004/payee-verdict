from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from payee_verdict.adapters import Adapter, build_adapters
from payee_verdict.cli import fixed_clock
from payee_verdict.engine import Clock
from payee_verdict.models import PayeeType, Payout, System
from payee_verdict.policy import Policy, load_policy
from payee_verdict.registry import Registry, load_registry

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "policies" / "default-v1.yaml"
STRICT_POLICY = ROOT / "policies" / "strict-v2.yaml"
REGISTRY = ROOT / "registry" / "synthetic-registry.yaml"
CORPUS = ROOT / "corpus" / "names-v1.yaml"

CURRENCY = {
    System.EU_VOP: "EUR",
    System.UK_COP: "GBP",
    System.IN_NAME_LOOKUP: "INR",
    System.BR_PIX: "BRL",
}


@pytest.fixture(scope="session")
def registry() -> Registry:
    return load_registry(REGISTRY)


@pytest.fixture(scope="session")
def policy() -> Policy:
    return load_policy(POLICY)


@pytest.fixture(scope="session")
def strict_policy() -> Policy:
    return load_policy(STRICT_POLICY)


@pytest.fixture()
def adapters(registry: Registry) -> dict[System, Adapter]:
    return build_adapters(registry)


@pytest.fixture()
def clock() -> Clock:
    return fixed_clock(datetime(2026, 10, 10, 9, 0, tzinfo=UTC))


def payout(
    system: System,
    account: str,
    name: str,
    payee_type: PayeeType = PayeeType.PERSON,
    amount: str = "100",
    *,
    bulk: bool = False,
    id_code: str | None = None,
    payout_id: str = "T-1",
) -> Payout:
    return Payout(
        payout_id=payout_id,
        system=system,
        account=account,
        payee_name=name,
        payee_type=payee_type,
        amount=Decimal(amount),
        currency=CURRENCY[system],
        bulk=bulk,
        payee_id_code=id_code,
    )
