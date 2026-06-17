from __future__ import annotations

import ssl
from pathlib import Path


def build_mtls_server_context(
    *,
    ca_file: str | Path | None = None,
    cert_file: str | Path | None = None,
    key_file: str | Path | None = None,
) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = False

    if ca_file is not None:
        context.load_verify_locations(cafile=str(ca_file))
    if cert_file is not None:
        context.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file) if key_file else None)
    return context
