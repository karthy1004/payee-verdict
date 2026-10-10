"""Simulated name-check systems. No adapter makes a network call."""

from __future__ import annotations

from payee_verdict.adapters.base import Adapter
from payee_verdict.adapters.eu_vop import EuVopAdapter
from payee_verdict.adapters.india import IndiaNameLookupAdapter
from payee_verdict.adapters.pix import PixKeyAdapter
from payee_verdict.adapters.uk_cop import UkCopAdapter
from payee_verdict.models import System
from payee_verdict.registry import Registry


def build_adapters(registry: Registry, *, india_available: bool = True) -> dict[System, Adapter]:
    return {
        System.EU_VOP: EuVopAdapter(registry.eu_vop),
        System.UK_COP: UkCopAdapter(registry.uk_cop),
        System.IN_NAME_LOOKUP: IndiaNameLookupAdapter(
            registry.in_name_lookup, available=india_available
        ),
        System.BR_PIX: PixKeyAdapter(registry.br_pix),
    }


__all__ = [
    "Adapter",
    "EuVopAdapter",
    "IndiaNameLookupAdapter",
    "PixKeyAdapter",
    "UkCopAdapter",
    "build_adapters",
]
