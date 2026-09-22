"""The Stripe error envelope: shapes, pairings, and the exact strings that are
quoted from wire observations.

These are unit tests of a pure module — no instance, no call — because there is
nothing stateful here: the constructors build an exception and `envelope()`
renders it. The boundary behavior (a raised error rolls the call back; a
returned decline keeps its writes) is exercised by later phases, which have
handlers to raise.
"""

from seahaven_stripe_world import stripe_errors as se


def test_the_type_enum_is_exactly_the_four_wire_values() -> None:
    """`rate_limit_error`, `authentication_error` and `permission_error` are
    stripe-python conveniences derived from the HTTP status and never appear on
    the wire."""
    assert {
        "api_error",
        "card_error",
        "idempotency_error",
        "invalid_request_error",
    } == se.ERROR_TYPES


def test_the_envelope_omits_every_none_field() -> None:
    error = se.resource_missing("customer", "cus_nope")
    assert error.envelope() == {
        "error": {
            "type": "invalid_request_error",
            "message": "No such customer: 'cus_nope'",
            "code": "resource_missing",
            "doc_url": "https://stripe.com/docs/error-codes/resource-missing",
        }
    }


def test_status_is_carried_not_derived() -> None:
    """400 and 404 share `invalid_request_error`; nothing in the enum distinguishes."""
    bad_request = se.unknown_parameter("emial")
    not_found = se.resource_missing("customer", "cus_nope")
    assert bad_request.type == not_found.type == "invalid_request_error"
    assert bad_request.status == 400
    assert not_found.status == 404


def test_doc_url_hyphenates_the_code_and_needs_a_code() -> None:
    error = se.internal("boom")
    assert error.type == "api_error"
    # `api_error` carries no doc_url even if a code were set: the link is for
    # the coded card and request faults.
    assert error.envelope()["error"] == {"type": "api_error", "message": "boom"}
    coded = se.invalid_request("no", code="parameter_unknown")
    assert coded.envelope()["error"]["doc_url"] == (
        "https://stripe.com/docs/error-codes/parameter-unknown"
    )


def test_resource_missing_pairs_with_a_param_when_given() -> None:
    error = se.resource_missing("customer", "cus_nope", param="starting_after")
    # The message is pinned here too: live probes return it without a trailing
    # period, and the period an earlier draft of the component design carried
    # is gone from both.
    assert error.message == "No such customer: 'cus_nope'"
    assert error.envelope()["error"]["param"] == "starting_after"
    assert error.code == "resource_missing"
    assert error.status == 404
    assert not error.pre_execution


def test_parameter_faults_are_pre_execution() -> None:
    """Stripe does not cache these under an idempotency key; the idempotency
    layer retracts the reservation when it sees this flag."""
    assert se.unknown_parameter("emial").pre_execution is True
    assert se.missing_parameter("amount").pre_execution is True
    assert se.unknown_parameter("emial").code == "parameter_unknown"
    assert se.unknown_parameter("emial").param == "emial"
    assert se.missing_parameter("amount").code == "parameter_missing"
    assert se.missing_parameter("amount").param == "amount"


def test_the_two_parameter_messages_match_the_wire_exactly() -> None:
    """Pinned verbatim, trailing period and all, because the two differ on it:
    the unknown-parameter probe returned no period, while `missing_parameter`'s
    period is corroborated (`gap-closure-2026-09-18.md`)."""
    assert se.unknown_parameter("emial").message == "Received unknown parameter: emial"
    assert se.missing_parameter("amount").message == "Missing required param: amount."


def test_the_three_cannot_expand_messages() -> None:
    exists = se.cannot_expand("application", exists=True)
    assert exists.envelope()["error"]["message"] == (
        "This property cannot be expanded (application)."
    )
    missing = se.cannot_expand("aplication", exists=False)
    assert missing.envelope()["error"]["message"] == (
        "This property cannot be expanded because it doesn't exist: aplication."
    )
    hinted = se.cannot_expand("customer", exists=True, hint="data")
    assert hinted.envelope()["error"]["message"] == (
        "This property cannot be expanded (customer). You may want to try "
        "expanding 'data.customer' instead."
    )
    # No code and no param on any of the three: the one complete wire body in
    # the research carries neither.
    for error in (exists, missing, hinted):
        body = error.envelope()["error"]
        assert "code" not in body
        assert "param" not in body
        assert error.status == 400
        assert error.pre_execution is True


def test_the_two_idempotency_errors() -> None:
    mismatch = se.idempotency_mismatch("retry-4471-a")
    assert mismatch.status == 400
    assert mismatch.type == "idempotency_error"
    assert mismatch.code is None
    assert mismatch.envelope() == {
        "error": {
            "type": "idempotency_error",
            "message": (
                "Keys for idempotent requests can only be used with the same "
                "parameters they were first used with. Try using a key other "
                "than 'retry-4471-a' if you meant to execute a different request."
            ),
        }
    }

    in_use = se.idempotency_key_in_use("abc123")
    assert in_use.status == 409
    assert in_use.code == "idempotency_key_in_use"
    assert in_use.envelope()["error"]["message"] == (
        "There is currently another in-progress request using this Idempotent Key "
        "(that probably means you submitted twice, and the other request is still "
        "going through)"
    )


def test_invalid_request_can_carry_a_status_overload() -> None:
    error = se.invalid_request("no such route", status=404)
    assert error.status == 404
    assert error.type == "invalid_request_error"


def test_every_error_type_is_reachable_by_a_constructor() -> None:
    """Three from live behavior (per `components/cross_cutting.md` §3.5.5) and
    one — `api_error` — from `internal()` only."""
    produced = {
        se.invalid_request("x").type,
        se.internal("x").type,
        se.idempotency_mismatch("k").type,
    }
    assert produced == {"invalid_request_error", "api_error", "idempotency_error"}


def test_sub_objects_and_the_extra_card_fields_survive_to_the_envelope() -> None:
    error = se.StripeApiError(
        402,
        "card_error",
        "Your card was declined.",
        code="card_declined",
        decline_code="insufficient_funds",
        charge="ch_123",
        doc_url=None,  # derived below from the code
        sub_objects={"payment_intent": {"id": "pi_123", "status": "requires_payment_method"}},
    )
    body = error.envelope()["error"]
    assert body["decline_code"] == "insufficient_funds"
    assert body["charge"] == "ch_123"
    assert body["doc_url"] == "https://stripe.com/docs/error-codes/card-declined"
    assert body["payment_intent"] == {"id": "pi_123", "status": "requires_payment_method"}


def test_pre_execution_defaults_false_and_survives() -> None:
    error = se.StripeApiError(402, "card_error", "declined")
    assert error.pre_execution is False
    assert se.invalid_request("x", pre_execution=True).pre_execution is True


# --- AST lint tests from §5.5 ----------------------------------------------------------------


def test_decline_is_never_raised() -> None:
    """AST lint: no `raise` whose operand calls `declined` — the function
    returns a dict, not an exception, and the type system carries the rule
    (`components/cross_cutting.md` §3.5.4)."""
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "seahaven_stripe_world"
    for py in sorted(src.rglob("*.py")):
        if "__pycache__" in str(py):
            continue
        tree = ast.parse(py.read_text(), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                if isinstance(node.exc, ast.Call) and isinstance(node.exc.func, ast.Attribute):
                    assert node.exc.func.attr != "declined", (
                        f"{py.name}:{node.lineno}: `raise` calls `declined`, which "
                        "returns a body and must not be raised"
                    )
                elif isinstance(node.exc, ast.Call) and isinstance(node.exc.func, ast.Name):
                    assert node.exc.func.id != "declined", (
                        f"{py.name}:{node.lineno}: `raise` calls `declined`, which "
                        "returns a body and must not be raised"
                    )


def test_envelope_is_the_only_boundary() -> None:
    """AST lint: `except StripeApiError` appears only in
    `middleware/stripe_envelope.py` (`components/cross_cutting.md` §3.5.3)."""
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "seahaven_stripe_world"
    allowed = {"stripe_envelope.py", "idempotency.py"}  # these two handle it by design
    for py in sorted(src.rglob("*.py")):
        if "__pycache__" in str(py):
            continue
        if py.name in allowed:
            continue
        tree = ast.parse(py.read_text(), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is not None:
                name = ""
                if isinstance(node.type, ast.Name):
                    name = node.type.id
                elif isinstance(node.type, ast.Attribute):
                    name = node.type.attr
                assert name != "StripeApiError", (
                    f"{py.name}:{node.lineno}: `except StripeApiError` outside the "
                    "boundary — only stripe_envelope.py and idempotency.py may catch it"
                )
