"""Customer-owned log subscription adapter. No reads or outbound network calls.

The Lambda role can only PutLogEvents to the precreated pilot stream. Preserve
the original provider message bytes; never summarize or reconstruct evidence.
"""
import base64
from datetime import datetime, timezone
import gzip
import io
import json
import os


def select(event, *, account, source_group, users, agent_id, now, expires_at):
    if now >= expires_at:
        return []
    encoded = event["awslogs"]["data"]
    if not isinstance(encoded, str) or len(encoded) > 8 * 1024 * 1024:
        raise ValueError("invalid_subscription_envelope")
    # CloudWatch is the authenticated invoker; bound decoded input as well.
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(encoded, validate=True))) as stream:
        decoded = stream.read(8 * 1024 * 1024 + 1)
    if len(decoded) > 8 * 1024 * 1024:
        raise ValueError("subscription_envelope_too_large")
    payload = json.loads(decoded)
    if payload.get("messageType") == "CONTROL_MESSAGE":
        return []
    if (payload.get("owner") != account or payload.get("logGroup") != source_group
            or payload.get("messageType") != "DATA_MESSAGE" or len(payload.get("logEvents", [])) > 10000):
        raise ValueError("unexpected_subscription_source")
    kept = []
    for event in payload["logEvents"]:
        try:
            body = json.loads(event["message"])
            if (body.get("logType") == "CHAT_LOGS" and body.get("accountId") == account
                    and body.get("user_arn") in users and body.get("agent_id") == agent_id
                    and type(event.get("timestamp")) is int and isinstance(event["message"], str)
                    and len(event["message"].encode()) <= 200000):
                kept.append({"timestamp": event["timestamp"], "message": event["message"]})
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return sorted(kept, key=lambda item: item["timestamp"])


def handler(event, context):
    import boto3
    selected = select(event, account=os.environ["ACCOUNT_ID"], source_group=os.environ["SOURCE_GROUP"],
        users=set(json.loads(os.environ["USERS"])), agent_id=os.environ["AGENT_ID"],
        now=datetime.now(timezone.utc), expires_at=datetime.fromisoformat(os.environ["EXPIRES_AT"].replace("Z", "+00:00")))
    client = boto3.client("logs")
    batch, size = [], 0
    for item in selected:
        current = len(item["message"].encode()) + 26
        if batch and (size + current > 900000 or item["timestamp"] - batch[0]["timestamp"] > 86400000):
            response = client.put_log_events(logGroupName=os.environ["DESTINATION_GROUP"], logStreamName="pilot-chat", logEvents=batch)
            if response.get("rejectedLogEventsInfo"):
                raise ValueError("pilot_log_delivery_rejected")
            batch, size = [], 0
        batch.append(item)
        size += current
    if batch:
        response = client.put_log_events(logGroupName=os.environ["DESTINATION_GROUP"], logStreamName="pilot-chat", logEvents=batch)
        if response.get("rejectedLogEventsInfo"):
            raise ValueError("pilot_log_delivery_rejected")
    return {"forwarded": len(selected)}
