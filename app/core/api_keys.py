"""API key generation, hashing, and verification (M3).

Threat model and design choices
--------------------------------

API keys are *machine credentials*, not human passwords. They are generated
server-side with high-entropy random bytes (32 bytes = 256 bits) and are
expected to be transmitted in a single Authorization header per request. They
are not subject to the low-entropy / brute-force concerns that motivate
memory-hard hashes like Argon2 or bcrypt for human passwords.

For this threat model, SHA-256 of the full token is an appropriate verification
primitive:

* The full token has 256 bits of entropy; brute-forcing a SHA-256 preimage is
  computationally infeasible.
* SHA-256 is fast: verification is O(1) and trivially scales for the lookup
  pattern (index on ``key_hash``).
* The full token is **never** stored. Only its SHA-256 hash is persisted.
* A leaked database reveals only hashes, not usable keys.

The token format is::

    syn_live_<8-char-public-prefix>_<43-char-secret-suffix>

The visible ``key_prefix`` stored in the DB is the first 18 characters
(``syn_live_`` + 8 random alphanumeric). The prefix is for display/lookup
only; it is not a secret and is not sufficient for authentication.

Verification is constant-time (``hmac.compare_digest``) to avoid timing
side-channels.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string

_PREFIX_NAMESPACE = "syn_live"
_PREFIX_RANDOM_LEN = 8
_SECRET_RANDOM_LEN = 32  # 32 bytes → 43 url-safe base64 chars without padding

# Visible prefix stored in DB: e.g. "syn_live_aB3xK9pQ" (18 chars).
_VISIBLE_PREFIX_LEN = len(_PREFIX_NAMESPACE) + 1 + _PREFIX_RANDOM_LEN  # 18

# Alphabet for the visible random prefix (safe for display; not a secret).
_PREFIX_ALPHABET = string.ascii_letters + string.digits


def _rand_alnum(n: int) -> str:
    return "".join(secrets.choice(_PREFIX_ALPHABET) for _ in range(n))


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key.

    Returns:
        (full_token, key_prefix, key_hash)

        * ``full_token`` — the complete secret token. Returned to the user
          exactly once, at creation time. NEVER store this.
        * ``key_prefix`` — the visible, non-secret prefix to display and
          index on. Safe to log.
        * ``key_hash`` — SHA-256 hex digest of the full token. Store this.
    """
    # The visible random part of the prefix.
    public_part = _rand_alnum(_PREFIX_RANDOM_LEN)
    # The secret suffix: 32 random bytes → 43 url-safe base64 chars.
    secret_suffix = secrets.token_urlsafe(_SECRET_RANDOM_LEN)
    full_token = f"{_PREFIX_NAMESPACE}_{public_part}_{secret_suffix}"
    key_prefix = full_token[:_VISIBLE_PREFIX_LEN]
    key_hash = hash_api_key(full_token)
    return full_token, key_prefix, key_hash


def hash_api_key(token: str) -> str:
    """Return the SHA-256 hex digest of a full API key token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_api_key(token: str, expected_hash: str) -> bool:
    """Constant-time verification of a token against a stored hash."""
    candidate = hash_api_key(token)
    return hmac.compare_digest(candidate, expected_hash)


def is_valid_format(token: str) -> bool:
    """Lightweight structural check; does NOT verify the secret."""
    if not token:
        return False
    # Format: syn_live_<8-char-public>_<43-char-secret>
    # The secret suffix is generated with secrets.token_urlsafe(32) which can
    # contain '-' and '_'. We split on the first three underscores only.
    parts = token.split("_", 3)
    if len(parts) != 4:
        return False
    if parts[0] != "syn" or parts[1] != "live":
        return False
    if len(parts[2]) != _PREFIX_RANDOM_LEN:
        return False
    if not all(c in _PREFIX_ALPHABET for c in parts[2]):
        return False
    # Secret suffix: 43 chars (url-safe base64 of 32 bytes, no padding)
    if len(parts[3]) < 32:
        return False
    return True


def visible_prefix(token: str) -> str:
    """Extract the visible, non-secret prefix from a token."""
    return token[:_VISIBLE_PREFIX_LEN]
