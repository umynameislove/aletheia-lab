from __future__ import annotations

import json

import pytest

from aletheia_lab.dashboard.bridge import (
    BACKEND_UNAVAILABLE,
    METHOD_PARAMETERS,
    dispatch,
)
from aletheia_lab.product import ProductError


class _FakeService:
    """Records each call and answers with a value naming the method."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def _record(self, method: str, **arguments: object) -> dict[str, object]:
        self.calls.append((method, arguments))
        return {"method": method}

    def demo_view(self) -> dict[str, object]:
        return self._record("demo_view")

    def preview_import(self, root: str) -> dict[str, object]:
        return self._record("preview_import", root=root)

    def confirm_import(self, preview_id: str, mapping: dict[str, object]) -> dict[str, object]:
        return self._record("confirm_import", preview_id=preview_id, mapping=mapping)

    def refresh(self, project_id: str) -> dict[str, object]:
        return self._record("refresh", project_id=project_id)

    def analyze_mock(self, project_id: str, snapshot_id: str, question: str) -> dict[str, object]:
        return self._record(
            "analyze_mock", project_id=project_id, snapshot_id=snapshot_id, question=question
        )

    def follow_up(
        self, result_id: str, selection_kind: str, selection_id: str, question: str
    ) -> dict[str, object]:
        return self._record(
            "follow_up",
            result_id=result_id,
            selection_kind=selection_kind,
            selection_id=selection_id,
            question=question,
        )

    def view(self, result_id: str) -> dict[str, object]:
        if result_id == "missing":
            raise ProductError("record_not_found", "The result was not found.")
        if result_id == "boom":
            raise RuntimeError("/private/store/secret-path exploded")
        return self._record("view", result_id=result_id)

    def export_report(self, result_id: str, format: str) -> bytes:  # noqa: A002
        self.calls.append(("export_report", {"result_id": result_id, "format": format}))
        return b"%PDF-1.4 bytes" if format == "pdf" else b"{}\n"

    def delete_project(self, project_id: str) -> dict[str, object]:
        return self._record("delete_project", project_id=project_id)


def _arguments(method: str) -> dict[str, object]:
    values: dict[str, object] = {name: f"{name}-1" for name in METHOD_PARAMETERS[method]}
    if "mapping" in values:
        values["mapping"] = {"baseline_run_id": "baseline"}
    if method == "export_report":
        values["format"] = "json"
    return values


@pytest.mark.parametrize("method", sorted(METHOD_PARAMETERS))
def test_every_contract_method_is_forwarded_with_its_exact_arguments(method: str) -> None:
    service = _FakeService()
    arguments = _arguments(method)

    response = dispatch(service, method, arguments)

    assert response.status == 200
    assert service.calls == [(method, arguments)]
    if method != "export_report":
        assert json.loads(response.body) == {"method": method}


def test_the_bridge_exposes_exactly_the_nine_service_methods() -> None:
    from aletheia_lab.product import ProductService

    public = {name for name in vars(ProductService) if not name.startswith("_")}
    assert set(METHOD_PARAMETERS) == public
    for method, names in METHOD_PARAMETERS.items():
        code = getattr(ProductService, method).__code__
        assert code.co_varnames[1 : code.co_argcount] == names


@pytest.mark.parametrize(
    ("method", "payload"),
    [
        ("view", {}),
        ("view", {"result_id": "r", "extra": "x"}),
        ("view", {"result_id": 7}),
        ("view", ["result_id"]),
        ("confirm_import", {"preview_id": "p", "mapping": "not-an-object"}),
    ],
)
def test_malformed_calls_are_refused_before_the_service(method: str, payload: object) -> None:
    service = _FakeService()

    response = dispatch(service, method, payload)

    assert response.status == 400
    assert json.loads(response.body)["code"] == "invalid_request"
    assert service.calls == []


def test_unknown_methods_are_refused() -> None:
    response = dispatch(_FakeService(), "__init__", {})

    assert response.status == 404
    assert json.loads(response.body)["code"] == "invalid_request"


def test_product_errors_keep_their_code_and_safe_message() -> None:
    response = dispatch(_FakeService(), "view", {"result_id": "missing"})

    assert response.status == 422
    assert json.loads(response.body) == {
        "code": "record_not_found",
        "safe_message": "The result was not found.",
    }


def test_unexpected_exceptions_leave_only_a_generic_code() -> None:
    response = dispatch(_FakeService(), "view", {"result_id": "boom"})

    assert response.status == 500
    assert json.loads(response.body)["code"] == "service_failure"
    assert b"private" not in response.body
    assert b"exploded" not in response.body


@pytest.mark.parametrize(
    ("report_format", "media_type"),
    [
        ("json", "application/json"),
        ("markdown", "text/markdown; charset=utf-8"),
        ("pdf", "application/pdf"),
    ],
)
def test_reports_are_returned_as_the_service_bytes(report_format: str, media_type: str) -> None:
    service = _FakeService()
    expected = service.export_report("r", report_format)

    response = dispatch(service, "export_report", {"result_id": "r", "format": report_format})

    assert response.status == 200
    assert response.content_type == media_type
    assert response.body == expected


def test_a_missing_backend_answers_every_call_with_a_safe_code() -> None:
    response = dispatch(BACKEND_UNAVAILABLE, "demo_view", {})

    assert response.status == 503
    assert json.loads(response.body)["code"] == "backend_unavailable"
