"""An offline Google Cloud Logging handler for tests: real serialization, recorded transport.

``real_handler`` builds a genuine ``CloudLoggingHandler`` (as locked) with an explicit ``Resource``
and anonymous credentials, so the SDK's own filter and payload shaping run, while the transport only
records what it would have sent. Callers must still forbid sockets and resource detection.
"""

from __future__ import annotations

from google.auth.credentials import AnonymousCredentials
from google.cloud.logging_v2.client import Client
from google.cloud.logging_v2.handlers import CloudLoggingHandler
from google.cloud.logging_v2.handlers.transports.base import Transport
from google.cloud.logging_v2.resource import Resource


class RecordingTransport(Transport):
    sent: list[dict] = []
    fail_send = False
    fail_close = False
    closed = 0

    def __init__(self, client, name, resource=None, **kwargs):
        self.client, self.name, self.resource = client, name, resource

    def send(self, record, message, **kwargs):
        if RecordingTransport.fail_send:
            raise ConnectionError("transport down")
        RecordingTransport.sent.append({"message": message, **kwargs})

    def flush(self):
        if RecordingTransport.fail_close:
            raise ConnectionError("flush failed")

    def close(self):
        RecordingTransport.closed += 1
        if RecordingTransport.fail_close:
            raise ConnectionError("close failed")


def reset() -> None:
    RecordingTransport.sent = []
    RecordingTransport.fail_send = RecordingTransport.fail_close = False
    RecordingTransport.closed = 0


def real_handler() -> CloudLoggingHandler:
    client = Client(project="offline-project", credentials=AnonymousCredentials(), _use_grpc=False)
    return CloudLoggingHandler(client, resource=Resource(type="global", labels={}), transport=RecordingTransport)
