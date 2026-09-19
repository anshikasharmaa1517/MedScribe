"""Shared TLS context for the urllib-based clients (Twilio, Bedrock Mantle).

python.org's macOS build ships without a CA bundle, so urllib fails every
HTTPS handshake with CERTIFICATE_VERIFY_FAILED. certifi fixes that where it is
installed (dev); Lambda's runtime has a system bundle and no certifi, so the
default context is used there.
"""
import ssl
from functools import lru_cache


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())
