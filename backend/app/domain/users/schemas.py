"""Pydantic input schemas for the users/auth domain (#252).

Lifted out of `app/api/routes/auth.py` so every domain has one
canonical `domain/<x>/schemas.py`.

Every input schema keeps `model_config = ConfigDict(extra="forbid")` —
`tests/test_extra_forbid.py` regression-tests this and a silent drop
would let unknown fields through.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field

__all__ = [
    "LoginIn",
    "PasswordResetIn",
    "RequestPasswordResetIn",
    "SignupIn",
    "VerifyIn",
]


class SignupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    workspace_name: str | None = None


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str


class VerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    token: str


class RequestPasswordResetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class PasswordResetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=200)
