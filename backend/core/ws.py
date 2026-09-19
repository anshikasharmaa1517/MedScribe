"""Push messages to the dashboard over the API Gateway WebSocket.

Best-effort by design: the REST path and the poll fallback remain correct if a
push fails, so a stale or dropped connection is cleaned up and otherwise
ignored rather than failing the doctor's request.
"""
import json
import logging

import boto3

from core.settings import WS_ENDPOINT

log = logging.getLogger(__name__)

_mgmt = None


def management_client(endpoint: str | None = None):
    global _mgmt
    if _mgmt is None:
        url = endpoint or WS_ENDPOINT
        if not url:
            return None
        _mgmt = boto3.client("apigatewaymanagementapi", endpoint_url=url)
    return _mgmt


def send(store, connection_id: str, message: dict, client=None) -> bool:
    client = client or management_client()
    if client is None:
        return False
    try:
        client.post_to_connection(ConnectionId=connection_id, Data=json.dumps(message).encode())
        return True
    except client.exceptions.GoneException:
        store.remove_connection(connection_id)
        return False
    except Exception as e:  # noqa: BLE001 - push is best-effort
        log.warning("ws push to %s failed: %s", connection_id, e)
        return False


def broadcast(store, consult_id: str, message: dict, client=None,
              exclude: str | None = None) -> int:
    client = client or management_client()
    if client is None:
        return 0
    sent = 0
    for cid in store.list_connections(consult_id):
        if cid != exclude and send(store, cid, message, client=client):
            sent += 1
    return sent


def draft_update(draft: dict) -> dict:
    return {"action": "draft.update", "draft": draft}


def draft_error(message: str) -> dict:
    return {"action": "draft.error", "error": message}
