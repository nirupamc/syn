"""Admin / management plane API schemas (M3).

Typed request/response models for the /admin/* endpoints. These are NOT
exposed to the data plane; they are management operations protected by a
separate bootstrap secret.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ---- users ------------------------------------------------------------------


class UserCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class UserOut(BaseModel):
    id: str
    name: str
    status: str
    created_at: datetime
    updated_at: datetime


# ---- clients ----------------------------------------------------------------


class ClientCreate(BaseModel):
    user_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    allowed_models: Optional[list[str]] = None


class ClientOut(BaseModel):
    id: str
    user_id: str
    name: str
    description: Optional[str]
    status: str
    allowed_models: list[str]
    created_at: datetime
    updated_at: datetime


# ---- api keys ---------------------------------------------------------------


class ApiKeyCreate(BaseModel):
    client_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=255)
    expires_at: Optional[datetime] = None


class ApiKeyCreateOut(BaseModel):
    """Response when a key is created/rotated: includes the full secret ONCE."""

    id: str
    name: str
    key_prefix: str
    key: str  # Full token. Returned ONLY at creation/rotation.
    client_id: str
    created_at: datetime
    expires_at: Optional[datetime]


class ApiKeyOut(BaseModel):
    """Listing metadata: NEVER includes the secret or hash."""

    id: str
    name: str
    key_prefix: str
    client_id: str
    created_at: datetime
    expires_at: Optional[datetime]
    revoked_at: Optional[datetime]
    last_used_at: Optional[datetime]


class ApiKeyRotateOut(ApiKeyCreateOut):
    """Rotation response: new key returned once, plus old key id if revoked."""

    rotated_from: Optional[str] = None
