from dataclasses import dataclass

from api_quality_agent.domain.models.endpoint_analysis import EndpointAnalysis
from api_quality_agent.domain.models.normalized_request import NormalizedRequest
from api_quality_agent.domain.models.postman_collection_items import CollectionRequest


@dataclass(frozen=True)
class AnalyzedCollectionRequest:
    raw_request: CollectionRequest
    # Já calculado por ApiAnalysisEngine (via PostmanRequestNormalizer) para
    # produzir `analysis`, mas descartado até aqui. Exposto para geradores
    # standalone (ex.: Playwright) que precisam montar a própria requisição
    # HTTP — método, URL, headers, auth, body — algo que `analysis`
    # (EndpointAnalysis) não carrega, porque o caminho Postman nunca precisou
    # disso (o script JS roda dentro de uma requisição que o Postman/Newman
    # já disparou). Nenhuma normalização nova acontece aqui.
    normalized_request: NormalizedRequest
    analysis: EndpointAnalysis
