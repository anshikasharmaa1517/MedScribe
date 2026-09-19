"""API Gateway WebSocket handler (spec step 14).

$connect            ?ticket=<from POST /consults/{id}/ws-ticket>  -> binds socket to consult
$disconnect                                                       -> cleanup
transcript.append   {action, text, speaker, seq}                  -> ack, then draft.update if due
draft.refresh       {action}                                      -> draft.update if due

Server -> client: {action: "ack"|"draft.update"|"draft.error"|"error", ...}

The same append/extract code as the REST API runs here, so the two transports
can never disagree about what a draft looks like.
"""
import json
import logging

log = logging.getLogger()
log.setLevel(logging.INFO)

from core import ws  # noqa: E402
from core.settings import EXTRACTION_INTERVAL_SECONDS  # noqa: E402
from handlers.api import (  # noqa: E402
    HttpError,
    append_transcript,
    get_draft,
    store,
)


def _reply(status: int, body: dict | None = None) -> dict:
    return {"statusCode": status, "body": json.dumps(body or {})}


def on_connect(event: dict) -> dict:
    ticket = (event.get("queryStringParameters") or {}).get("ticket", "")
    binding = store().take_ws_ticket(ticket) if ticket else None
    if not binding:
        return _reply(401, {"error": "invalid or expired ticket"})
    store().add_connection(event["requestContext"]["connectionId"],
                           binding["consultId"], binding["doctorId"])
    return _reply(200)


def on_disconnect(event: dict) -> dict:
    store().remove_connection(event["requestContext"]["connectionId"])
    return _reply(200)


def _bound_consult(connection_id: str) -> dict:
    conn = store().get_connection(connection_id)
    if not conn:
        raise HttpError(401, "connection is not bound to a consult")
    c = store().get_consult_by_id(conn["consultId"])
    if not c or c.get("doctorId") != conn["doctorId"]:
        raise HttpError(404, "consult not found")
    return c


def _push_draft_if_due(c: dict, connection_id: str) -> bool:
    """Run extraction if due and broadcast the result. Returns True if a pass ran."""
    before = c.get("lastExtractedAt") or 0
    try:
        draft = get_draft(c)
    except Exception as e:  # noqa: BLE001 - surface to the socket, never crash the consult
        log.exception("extraction failed on websocket path")
        ws.send(store(), connection_id, ws.draft_error(str(e)))
        return True
    after = (store().get_consult_by_id(c["consultId"]) or {}).get("lastExtractedAt") or 0
    if after == before:
        return False
    if draft.get("extraction_error"):
        ws.broadcast(store(), c["consultId"], ws.draft_error(draft["extraction_error"]))
    else:
        ws.broadcast(store(), c["consultId"], ws.draft_update(draft))
    return True


def on_transcript_append(event: dict, body: dict) -> dict:
    connection_id = event["requestContext"]["connectionId"]
    c = _bound_consult(connection_id)
    out = append_transcript(c, body)
    ws.send(store(), connection_id, {"action": "ack", "seq": out["seq"],
                                     "nextRefreshMs": EXTRACTION_INTERVAL_SECONDS * 1000})
    _push_draft_if_due(store().get_consult_by_id(c["consultId"]), connection_id)
    return _reply(200)


def on_draft_refresh(event: dict) -> dict:
    connection_id = event["requestContext"]["connectionId"]
    c = _bound_consult(connection_id)
    if not _push_draft_if_due(c, connection_id):
        ws.send(store(), connection_id, ws.draft_update(c.get("draft") or {}))   # nothing new
    return _reply(200)


def handler(event, context):
    route = event.get("requestContext", {}).get("routeKey")
    try:
        if route == "$connect":
            return on_connect(event)
        if route == "$disconnect":
            return on_disconnect(event)
        body = json.loads(event.get("body") or "{}")
        if route == "transcript.append":
            return on_transcript_append(event, body)
        if route == "draft.refresh":
            return on_draft_refresh(event)
        ws.send(store(), event["requestContext"]["connectionId"],
                {"action": "error", "error": f"unknown action {route!r}"})
        return _reply(400, {"error": "unknown action"})
    except HttpError as e:
        cid = event.get("requestContext", {}).get("connectionId")
        if cid and route not in ("$connect", "$disconnect"):
            ws.send(store(), cid, {"action": "error", "error": str(e), "status": e.status})
        return _reply(e.status, {"error": str(e)})
    except Exception as e:  # noqa: BLE001
        log.exception("unhandled websocket error")
        return _reply(500, {"error": f"internal error: {type(e).__name__}"})
