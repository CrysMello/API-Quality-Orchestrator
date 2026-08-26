from dataclasses import dataclass


@dataclass(frozen=True)
class TraceArtifact:
    # Evidência de um Playwright Trace persistido para um teste que FALHOU
    # (P1.3 — Trace em falha). Nunca gerado para um teste que passou (regra
    # explícita: "evitar manter artefatos desnecessários" — ver conftest.py
    # gerado, que só chama tracing.stop(path=...) no caminho de falha).
    #
    # O conteúdo binário do trace NUNCA fica dentro do ExecutionResult/
    # result.json — só esta referência. O .zip em si vive num arquivo
    # externo, mascarado por PlaywrightAdapter (ver
    # adapters/playwright/trace_masking.py) antes de qualquer persistência.
    #
    # Campo "type": sempre "playwright-trace" nesta fase — nunca outro
    # valor inventado (campo aberto para futuros tipos de artefato, sem
    # forçar todo consumidor a assumir que só existe um tipo possível).
    type: str
    # Mesma chave usada por HttpTransaction.test_id/AssertionResult.test_id
    # — é o que permite reconstruir test_id -> trace, nunca o nome do
    # arquivo sozinho (regra explícita do bloco).
    test_id: str
    # Antes da persistência (ExecutionResult "ao vivo", saído do
    # PlaywrightAdapter): caminho ABSOLUTO de um arquivo temporário já
    # mascarado. Depois de persistido (dentro do result.json, via
    # PersistExecutionResultUseCase): caminho RELATIVO ao diretório do
    # result.json (ex.: "traces/xxx.zip") — as duas fases nunca se
    # confundem: cada camada só produz uma delas.
    path: str
