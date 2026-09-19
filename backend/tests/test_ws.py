"""WebSocket handler: ticket handshake, append/refresh push, cleanup, REST fan-out."""
import json
import os

import pytest
from moto import mock_aws

from core import store as store_mod
from core import ws as ws_mod
from core.store import Store, create_tables
from scripts import seed_demo_data
from tests.test_api_handler import call, start
from tests.test_pipeline import SAMPLE, StubLLM


class FakeMgmt:
    """Stands in for apigatewaymanagementapi; records pushes, can simulate a gone socket."""

    class exceptions:  # noqa: N801 - mirrors boto3's shape
        class GoneException(Exception):
            pass

    def __init__(self):
        self.sent = []
        self.gone = set()

    def post_to_connection(self, ConnectionId, Data):
        if ConnectionId in self.gone:
            raise self.exceptions.GoneException()
        self.sent.append((ConnectionId, json.loads(Data)))

    def by_conn(self, cid):
        return [m for c, m in self.sent if c == cid]


@pytest.fixture
def env(monkeypatch):
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("SSM_PREFIX", "")
    with mock_aws():
        db = store_mod.dynamodb_resource(endpoint_url=None, region="ap-south-1")
        create_tables(db)
        st = Store(db)
        seed_demo_data.seed(st)
        from handlers import api, ws

        mgmt = FakeMgmt()
        monkeypatch.setattr(api, "_store", st)
        monkeypatch.setattr(api, "_client", StubLLM(SAMPLE))
        monkeypatch.setattr(api, "EXTRACTION_INTERVAL_SECONDS", 0)
        monkeypatch.setattr(ws, "EXTRACTION_INTERVAL_SECONDS", 0)
        monkeypatch.setattr(ws_mod, "_mgmt", mgmt)
        yield api, ws, st, mgmt


def ws_event(route, connection_id, body=None, query=None):
    return {
        "requestContext": {"routeKey": route, "connectionId": connection_id},
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
    }


def append(ws, cid, text, seq):
    body = {"action": "transcript.append", "text": text, "speaker": "doctor", "seq": seq}
    return ws.handler(ws_event("transcript.append", cid, body), None)


def connect(api, ws, cid, consult_id):
    status, t = call(api, "POST", f"/consults/{consult_id}/ws-ticket")
    assert status == 200 and t["expires_in"] == 60
    res = ws.handler(ws_event("$connect", cid, query={"ticket": t["ticket"]}), None)
    assert res["statusCode"] == 200
    return t["ticket"]


def test_connect_requires_valid_single_use_ticket(env):
    api, ws, st, _ = env
    consult_id = start(api)
    ticket = connect(api, ws, "c1", consult_id)
    # same ticket again -> refused; no ticket -> refused
    reused = ws.handler(ws_event("$connect", "c2", query={"ticket": ticket}), None)
    assert reused["statusCode"] == 401
    assert ws.handler(ws_event("$connect", "c3"), None)["statusCode"] == 401
    assert st.list_connections(consult_id) == ["c1"]
    # a ticket cannot be minted for another doctor's consult or a finished one
    assert call(api, "POST", f"/consults/{consult_id}/ws-ticket", doctor="other")[0] == 404


def test_append_acks_and_pushes_draft_update_to_all_sockets(env):
    api, ws, st, mgmt = env
    consult_id = start(api)
    connect(api, ws, "c1", consult_id)
    connect(api, ws, "c2", consult_id)

    res = append(ws, "c1", "Dolo 650 le lena", 1)
    assert res["statusCode"] == 200
    ack = mgmt.by_conn("c1")[0]
    assert ack["action"] == "ack" and ack["seq"] == 1 and "nextRefreshMs" in ack
    updates = [m for c, m in mgmt.sent if m["action"] == "draft.update"]
    assert {c for c, m in mgmt.sent if m["action"] == "draft.update"} == {"c1", "c2"}
    labels = [m["matched"] for m in updates[0]["draft"]["medicines"]]
    assert labels == ["Dolo 650mg", "Cetzine 10mg"]
    assert st.get_consult_by_id(consult_id)["transcript"][0]["text"] == "Dolo 650 le lena"


def test_refresh_only_pushes_when_extraction_ran(env, monkeypatch):
    api, ws, st, mgmt = env
    consult_id = start(api)
    connect(api, ws, "c1", consult_id)
    append(ws, "c1", "x", 1)
    mgmt.sent.clear()

    monkeypatch.setattr(api, "EXTRACTION_INTERVAL_SECONDS", 3600)
    append(ws, "c1", "y", 2)
    assert [m["action"] for m in mgmt.by_conn("c1")] == ["ack"]          # not due: no draft push

    ws.handler(ws_event("draft.refresh", "c1", {"action": "draft.refresh"}), None)
    assert mgmt.by_conn("c1")[-1]["action"] == "draft.update"           # refresh always answers


def test_extraction_failure_is_reported_as_draft_error(env, monkeypatch):
    from core.llm import LLMAPIError

    api, ws, _, mgmt = env
    consult_id = start(api)
    connect(api, ws, "c1", consult_id)
    monkeypatch.setattr(api, "_client", StubLLM(error=LLMAPIError("down")))
    append(ws, "c1", "x", 1)
    actions = [m["action"] for m in mgmt.by_conn("c1")]
    assert actions == ["ack", "draft.error"]
    assert mgmt.by_conn("c1")[1]["error"] == "down"


def test_disconnect_and_gone_sockets_are_cleaned_up(env):
    api, ws, st, mgmt = env
    consult_id = start(api)
    connect(api, ws, "c1", consult_id)
    connect(api, ws, "c2", consult_id)
    mgmt.gone.add("c2")
    append(ws, "c1", "x", 1)
    assert st.list_connections(consult_id) == ["c1"]                   # c2 pruned on GoneException
    ws.handler(ws_event("$disconnect", "c1"), None)
    assert st.list_connections(consult_id) == [] and st.get_connection("c1") is None


def test_unbound_connection_and_unknown_action(env):
    api, ws, _, mgmt = env
    res = ws.handler(ws_event("transcript.append", "ghost", {"text": "x"}), None)
    assert res["statusCode"] == 401 and mgmt.by_conn("ghost")[0]["action"] == "error"
    consult_id = start(api)
    connect(api, ws, "c1", consult_id)
    assert ws.handler(ws_event("nope", "c1", {}), None)["statusCode"] == 400


def test_rest_patch_fans_out_to_sockets(env):
    api, ws, _, mgmt = env
    consult_id = start(api)
    connect(api, ws, "c1", consult_id)
    append(ws, "c1", "x", 1)
    mgmt.sent.clear()
    call(api, "PATCH", f"/consults/{consult_id}/draft", {"field": "diagnosis", "value": "dengue"})
    pushed = mgmt.by_conn("c1")
    assert pushed and pushed[-1]["action"] == "draft.update"
    assert pushed[-1]["draft"]["diagnosis"] == "dengue"
