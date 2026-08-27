"""Internal error-model tests."""

from __future__ import annotations

import pytest

from app.core.errors import (
    BackendUnavailableError,
    NotFoundError,
    SynError,
    ValidationError,
)


def test_syn_error_defaults():
    err = SynError()
    assert err.code == "syn_error"
    assert err.http_status == 500


def test_subclass_code_and_status():
    assert NotFoundError().code == "not_found"
    assert NotFoundError().http_status == 404
    assert ValidationError().http_status == 400
    assert BackendUnavailableError().http_status == 502


def test_custom_message_and_overrides():
    err = NotFoundError(
        "model not found",
        code="model_not_found",
        http_status=404,
        request_id="abc",
    )
    assert err.detail == "model not found"
    assert err.code == "model_not_found"
    assert err.http_status == 404
    assert err.request_id == "abc"


def test_to_dict():
    err = ValidationError(detail="bad payload", request_id="req-1")
    payload = err.to_dict()
    assert payload == {
        "code": "validation_error",
        "detail": "bad payload",
        "request_id": "req-1",
    }


def test_to_dict_without_request_id():
    err = BackendUnavailableError(detail="backend down")
    assert "request_id" not in err.to_dict()
