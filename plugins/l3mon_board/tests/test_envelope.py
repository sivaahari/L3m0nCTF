"""The answer shapes of the player endpoints (the contract's section 1): a success is {success, data}, an error is {success: false, error,
message, [retry_after], [errors], [phase], request_id, ...extra} with the keys in that order, and every answer carries X-Request-Id
equal to the request_id (a person can quote it and the log line has it).

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board
"""
import json
import re

import pytest

from CTFd.plugins.l3mon_board import envelope
from tests.helpers import create_ctfd, destroy_ctfd

ID = re.compile(r"^[0-9A-F]{4}-[0-9A-F]{4}$")


@pytest.fixture()
def app():
    app = create_ctfd(enable_plugins=True, user_mode="teams")
    yield app
    destroy_ctfd(app)


def test_an_error_has_the_contracts_keys_in_the_contracts_order_and_the_request_id_is_also_a_header(app):
    with app.test_request_context("/"):
        response = envelope.fail("phase_closed", 403, "Programmes go on air when the broadcast starts.", phase="before")
        body = json.loads(response.get_data(as_text=True), object_pairs_hook=list)
        assert [k for k, _ in body] == ["success", "error", "message", "phase", "request_id"]
        body = dict(body)
        assert body["success"] is False and body["error"] == "phase_closed" and response.status_code == 403
        assert ID.match(body["request_id"]) and response.headers["X-Request-Id"] == body["request_id"]
        assert response.headers["Cache-Control"] == "no-store"


def test_a_retry_after_is_in_the_body_and_in_the_header_and_extra_keys_come_last(app):
    with app.test_request_context("/"):
        response = envelope.fail("ratelimited", 429, "Slow down.", retry_after=8, others=[{"slug": "x"}])
        body = json.loads(response.get_data(as_text=True), object_pairs_hook=list)
        assert [k for k, _ in body] == ["success", "error", "message", "retry_after", "request_id", "others"]
        assert response.headers["Retry-After"] == "8" and dict(body)["retry_after"] == 8


def test_field_errors_come_before_the_phase_and_the_request_id(app):
    with app.test_request_context("/"):
        response = envelope.fail("invalid", 400, "Check the form.", errors={"submission": ["Type a flag first."]}, phase="live")
        body = json.loads(response.get_data(as_text=True), object_pairs_hook=list)
        assert [k for k, _ in body] == ["success", "error", "message", "errors", "phase", "request_id"]


def test_a_success_is_success_and_data_and_never_cached_unless_asked(app):
    with app.test_request_context("/"):
        response = envelope.ok({"ver": "1"})
        assert json.loads(response.get_data(as_text=True)) == {"success": True, "data": {"ver": "1"}}
        assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store" and ID.match(response.headers["X-Request-Id"])
        cached = envelope.ok({"x": 1}, etag='"b123"')
        assert cached.headers["ETag"] == '"b123"' and cached.headers["Cache-Control"] == "private, no-cache"


def test_the_request_id_is_the_same_all_through_one_request_and_new_for_the_next(app):
    with app.test_request_context("/"):
        first = envelope.request_id()
        assert envelope.request_id() == first
        assert envelope.ok({}).headers["X-Request-Id"] == first and envelope.fail("not_found", 404, "x").headers["X-Request-Id"] == first
    with app.test_request_context("/"):
        assert envelope.request_id() != first


def test_a_not_modified_answer_has_no_body_and_keeps_the_etag(app):
    with app.test_request_context("/"):
        response = envelope.not_modified('"b123"')
        assert response.status_code == 304 and response.get_data() == b"" and response.headers["ETag"] == '"b123"'
        assert response.headers["Cache-Control"] == "private, no-cache"


@pytest.mark.parametrize(
    "header,etag,expected",
    [
        (None, '"b1"', False),
        ("", '"b1"', False),
        ('"b1"', '"b1"', True),
        ('W/"b1"', '"b1"', True),
        ('"b0", "b1"', '"b1"', True),
        ('"b0", "b1"', '"b0"', True),
        ('"b0", "b2"', '"b1"', False),
        ("b1", '"b1"', False),  # an ETag is quoted; a bare word is not one
        ('"b0","b1"', '"b1"', True),
        ("*", '"b1"', True),
        ('"b2"', '"b1"', False),
    ],
)
def test_if_none_match_accepts_a_list_weak_forms_and_a_star(header, etag, expected):
    assert envelope.etag_matches(header, etag) is expected
