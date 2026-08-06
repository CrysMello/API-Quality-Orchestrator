from api_quality_agent.domain.models import NormalizedUrl


def derive_base_url(url: NormalizedUrl) -> str | None:
    # Nunca inventa um host — só deriva de protocol+host já normalizados
    # (evidência real do NormalizedRequest). None quando não há o
    # suficiente para montar uma URL (ex.: request só com path relativo).
    # Resolução de variáveis ({{baseUrl}} etc.) é escopo de uma etapa
    # futura — aqui é só o literal já presente no request normalizado.
    if not url.protocol or not url.host:
        return None
    return f"{url.protocol}://{'.'.join(url.host)}"
