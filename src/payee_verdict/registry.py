"""Synthetic account registries that the simulated adapters look names up in.

Every account, name and key in the shipped registry is invented. Identifiers use
obviously fake formats (IBAN-like strings containing ``TEST``, sort codes starting
``99``, IFSC-like codes starting ``TEST``, PIX keys on ``.example`` domains or all-zero
UUIDs) so none can be mistaken for a real account.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from payee_verdict.models import PayeeType


class RegistryError(ValueError):
    pass


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    type: PayeeType


class EuAccount(_Entry):
    iban: str
    id_codes: dict[str, str] = Field(default_factory=dict)
    latency_ms: int = Field(default=250, ge=0)


class UkAccount(_Entry):
    sort_code: str = Field(pattern=r"^\d{2}-\d{2}-\d{2}$")
    account_number: str = Field(pattern=r"^\d{8}$")
    opted_out: bool = False

    @property
    def account(self) -> str:
        return f"{self.sort_code}:{self.account_number}"


class InAccount(_Entry):
    ifsc: str = Field(pattern=r"^[A-Z]{4}0[A-Z0-9]{6}$")
    account_number: str
    lookup_supported: bool = True

    @property
    def account(self) -> str:
        return f"{self.ifsc}:{self.account_number}"


class PixKey(_Entry):
    key: str
    key_type: str = Field(pattern=r"^(EMAIL|PHONE|EVP)$")
    flagged: bool = False


class Registry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    eu_vop: tuple[EuAccount, ...] = ()
    uk_cop: tuple[UkAccount, ...] = ()
    in_name_lookup: tuple[InAccount, ...] = ()
    br_pix: tuple[PixKey, ...] = ()

    @model_validator(mode="after")
    def _unique_accounts(self) -> Registry:
        for label, keys in (
            ("eu_vop", [a.iban for a in self.eu_vop]),
            ("uk_cop", [a.account for a in self.uk_cop]),
            ("in_name_lookup", [a.account for a in self.in_name_lookup]),
            ("br_pix", [k.key for k in self.br_pix]),
        ):
            if len(keys) != len(set(keys)):
                raise ValueError(f"{label}: duplicate account identifiers")
        return self


def load_registry(path: str | Path) -> Registry:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return Registry.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise RegistryError(f"cannot load registry {path}: {exc}") from exc
