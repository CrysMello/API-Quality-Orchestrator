"""Parte 08 do plano de ação Playwright: derive_base_url — nunca inventa um
host, só deriva de protocol+host já normalizados.
"""

from api_quality_agent.domain.models import NormalizedUrl
from api_quality_agent.generators.playwright import derive_base_url


def _url(**overrides) -> NormalizedUrl:
    defaults = {
        "raw": None,
        "protocol": None,
        "host": (),
        "path": (),
        "query_parameters": (),
        "variables": (),
    }
    defaults.update(overrides)
    return NormalizedUrl(**defaults)


def test_derives_base_url_from_protocol_and_host():
    url = _url(protocol="https", host=("api", "exemplo", "com"))

    assert derive_base_url(url) == "https://api.exemplo.com"


def test_returns_none_without_protocol():
    url = _url(protocol=None, host=("api", "exemplo", "com"))

    assert derive_base_url(url) is None


def test_returns_none_without_host():
    url = _url(protocol="https", host=())

    assert derive_base_url(url) is None


def test_never_includes_path_or_query():
    url = _url(
        protocol="https",
        host=("api", "exemplo", "com"),
        path=("users", "1"),
        query_parameters=(),
    )

    assert derive_base_url(url) == "https://api.exemplo.com"
