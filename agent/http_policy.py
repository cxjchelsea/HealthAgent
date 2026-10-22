"""HTTP client policy for model backends.

Native Ollama bound to loopback must not inherit host HTTP proxy settings.
Remote endpoints retain standard proxy environment support.
"""
from urllib.parse import urlsplit

def model_http_trust_env(provider: str, base_url: str) -> bool:
    if provider != "ollama_proxy":
        return True
    url = urlsplit(base_url)
    return url.scheme not in ("http", "https") or url.hostname not in ("127.0.0.1", "localhost", "::1")
