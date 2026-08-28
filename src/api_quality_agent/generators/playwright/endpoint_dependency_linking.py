"""Linkagem de dependências entre endpoints Playwright (variável produzida
por um endpoint, consumida por outro em runtime).

Etapa de PREPARAÇÃO — roda ANTES da geração de cada arquivo, nunca dentro
de PlaywrightEndpointTestGenerator (que nunca descobre outros endpoints
sozinho, por contrato). Chamada por GeneratePlaywrightTestSuiteUseCase
depois que TODAS as TestStrategy/NormalizedRequest de uma suíte já foram
montadas (só então é possível saber quem produz o quê).

Escopo desta implementação: apenas segmentos de PATH (`:nome`/`{nome}`,
nunca `{{variável}}` do Postman — essa continua resolvida por
VariableResolutionSession normalmente) sem default declarado na
Collection — exatamente o caso que hoje cai em "variável não resolvida"
(ver _resolve_path_segments em playwright_endpoint_test_generator.py).
`VariableUsage.location` já prevê query/header/body para uma extensão
futura, mas só "path" é produzido aqui.

Regra de correspondência (ver _nearest_producer): o endpoint MAIS PRÓXIMO
(na ordem original da Collection, excluindo o próprio) cujo
VariableExtraction.variable_name bate exatamente com o nome do segmento
de path — preferindo um produtor que já viria ANTES, senão o mais próximo
que viria DEPOIS. Determinística e simples, mas nunca "o primeiro
declarado na Collection inteira": isolamento (Etapa 9) exige que duas
cadeias independentes usando o MESMO nome de variável (ex.: dois recursos
diferentes ambos extraindo um campo "id") nunca colidam — "mais próximo"
resolve isso; "primeiro globalmente" não.

Ciclos (A depende de B que depende de A, direto ou por uma cadeia maior)
nunca viram uma ordem arbitrária: TODA aresta de um ciclo é removida (ver
_find_one_cycle — reconstrói o ciclo inteiro percorrido, nunca só a última
aresta que o fechou), repetindo a busca até o grafo restante ficar
acíclico (pode haver mais de um ciclo disjunto na mesma suíte). Os
endpoints envolvidos voltam a cair no fallback de variável não resolvida
de sempre, com um warning explícito (CIRCULAR_VARIABLE_DEPENDENCY) em vez
de uma escolha arbitrária de ordem.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from api_quality_agent.domain.models import NormalizedRequest, VariableExtraction, VariableUsage
from api_quality_agent.generators.playwright.endpoint_file_naming import (
    endpoint_source_to_slug,
    is_parameterized_segment,
    parameterized_segment_key,
)
from api_quality_agent.generators.playwright.playwright_generation_warning import (
    PlaywrightGenerationWarning,
)
from api_quality_agent.generators.playwright.variable_resolver import extract_pure_variable_name
from api_quality_agent.generators.playwright.warning_catalog import CIRCULAR_VARIABLE_DEPENDENCY

_LOCATION_PATH = "path"


def producer_test_id_for(endpoint_source: str) -> str:
    # Mesmo esquema de nome que PlaywrightEndpointTestGenerator usa para a
    # função de teste "success" de um endpoint (Parte 07) — nunca uma
    # segunda fonte de verdade para esse identificador. Calculado aqui a
    # partir só do endpoint_source (disponível antes da geração em si),
    # nunca do conteúdo já gerado.
    return f"test_{endpoint_source_to_slug(endpoint_source)}_success"


@dataclass(frozen=True)
class EndpointDependencyInput:
    endpoint_source: str
    normalized_request: NormalizedRequest
    variable_extractions: tuple[VariableExtraction, ...]


@dataclass(frozen=True)
class LinkedEndpoint:
    # Resultado da linkagem para UM endpoint de entrada — nunca decide
    # nada de geração em si, só entrega o que o gerador precisa
    # (variable_usages, quando este endpoint é consumidor) e o que ele
    # deve tratar como "reivindicado" (claimed_extraction_names, quando
    # este endpoint é produtor de algo que um outro endpoint realmente
    # usa) — o resto de TestStrategy permanece intocado.
    variable_usages: tuple[VariableUsage, ...] = ()
    claimed_extraction_names: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class DependencyLinkingResult:
    # Uma entrada por endpoint de entrada, na MESMA ordem/tamanho da lista
    # recebida por link_endpoint_dependencies.
    linked_endpoints: tuple[LinkedEndpoint, ...]
    # Ordem final, como ÍNDICES na lista de entrada — endpoints sem
    # nenhuma relação de dependência mantêm sua posição relativa original
    # (nunca reordenados artificialmente só por estarem na mesma suíte).
    order: tuple[int, ...]
    warnings: tuple[PlaywrightGenerationWarning, ...]


def _nearest_producer(consumer_index: int, candidate_indices: Sequence[int]) -> int | None:
    # Isolamento (Etapa 9): duas cadeias independentes que por acaso usam o
    # MESMO nome de variável (ex.: "POST /customers" e "POST /products" os
    # dois extraindo um campo chamado "id") nunca podem colidir — o
    # candidato MAIS PRÓXIMO (nunca "o primeiro declarado na Collection
    # inteira") é sempre o produtor certo para um consumidor, porque é
    # assim que uma Collection encadeada é naturalmente escrita: o produtor
    # de uma cadeia fica perto de quem o consome, não longe. Preferência:
    # (1) produtor mais próximo que já viria ANTES na ordem original —
    # caso comum, cadeia escrita na ordem natural; (2) na ausência de um
    # produtor anterior, o mais próximo que viria DEPOIS (Etapa 7 calcula a
    # ordem de execução a partir da dependência, não o contrário — declarar
    # fora de ordem na Collection não impede a linkagem).
    excluding_self = [index for index in candidate_indices if index != consumer_index]
    preceding = [index for index in excluding_self if index < consumer_index]
    if preceding:
        return max(preceding)
    following = [index for index in excluding_self if index > consumer_index]
    if following:
        return min(following)
    return None


def link_endpoint_dependencies(
    endpoints: Sequence[EndpointDependencyInput],
) -> DependencyLinkingResult:
    producer_by_variable_name: dict[str, list[int]] = {}
    for index, endpoint in enumerate(endpoints):
        for extraction in endpoint.variable_extractions:
            producer_by_variable_name.setdefault(extraction.variable_name, []).append(index)

    # consumer_index -> {variable_name: producer_index} — um consumidor
    # pode depender de mais de uma variável (uma por segmento de path sem
    # default), cada uma com seu próprio produtor.
    dependencies: dict[int, dict[str, int]] = {}
    for consumer_index, endpoint in enumerate(endpoints):
        for segment in endpoint.normalized_request.url.path:
            if not is_parameterized_segment(segment):
                continue
            # {{nome}} (variável Postman de verdade) também bate com o
            # reconhecimento de segmento parametrizado — nunca tratado
            # como "produzido por outro teste" (mesmo critério já usado
            # por _resolve_path_segments).
            if extract_pure_variable_name(segment) is not None:
                continue
            key = parameterized_segment_key(segment)
            if not key:
                continue
            # Já tem um default literal na própria Collection
            # (url.variable[]) — resolvido por VariableResolutionSession
            # normalmente, nunca uma dependência de runtime.
            has_collection_default = any(
                variable.key == key and variable.value
                for variable in endpoint.normalized_request.url.variables
            )
            if has_collection_default:
                continue
            producer_index = _nearest_producer(
                consumer_index, producer_by_variable_name.get(key, ())
            )
            if producer_index is None:
                continue
            dependencies.setdefault(consumer_index, {})[key] = producer_index

    cyclic_edges, cycle_warnings = _detect_cycles(dependencies, endpoints)
    for consumer_index, variable_name in cyclic_edges:
        del dependencies[consumer_index][variable_name]
        if not dependencies[consumer_index]:
            del dependencies[consumer_index]

    order = _topological_order(len(endpoints), dependencies)

    linked_endpoints = _build_linked_endpoints(endpoints, dependencies)
    return DependencyLinkingResult(
        linked_endpoints=linked_endpoints, order=order, warnings=cycle_warnings
    )


def _consumers_of(dependencies: dict[int, dict[str, int]]) -> dict[int, list[tuple[int, str]]]:
    # Grafo "produtor -> consumidor" (produtor precisa rodar antes) — a
    # mesma direção usada por _topological_order (Kahn).
    consumers_of: dict[int, list[tuple[int, str]]] = {}
    for consumer_index, by_variable in dependencies.items():
        for variable_name, producer_index in by_variable.items():
            consumers_of.setdefault(producer_index, []).append((consumer_index, variable_name))
    return consumers_of


def _find_one_cycle(
    consumers_of: dict[int, list[tuple[int, str]]],
) -> list[tuple[int, str]] | None:
    # Devolve TODAS as arestas de UM ciclo completo (nunca só a "back edge"
    # que fecharia o laço numa DFS clássica) — a reconstrução caminha o
    # `path` atual desde o nó onde o ciclo fecha até o nó corrente, mais a
    # aresta que fechou o laço. Isto é deliberado: um ciclo inteiro (A -> B
    # -> C -> A) precisa sair TODO sem VariableUsage nenhum (regra 8 do
    # enunciado — "não gerar uma cadeia incorreta"), nunca só a última
    # aresta percorrida, que deixaria as demais arestas do MESMO ciclo
    # como se fossem dependências válidas.
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[int, int] = {}
    path: list[int] = []
    path_edges: list[tuple[int, str]] = []

    def visit(node: int) -> list[tuple[int, str]] | None:
        color[node] = GRAY
        path.append(node)
        for consumer_index, variable_name in consumers_of.get(node, ()):
            state = color.get(consumer_index, WHITE)
            if state == GRAY:
                start = path.index(consumer_index)
                cycle_edges = list(path_edges[start:])
                cycle_edges.append((consumer_index, variable_name))
                return cycle_edges
            if state == WHITE:
                path_edges.append((consumer_index, variable_name))
                found = visit(consumer_index)
                if found is not None:
                    return found
                path_edges.pop()
        path.pop()
        color[node] = BLACK
        return None

    for node in list(consumers_of.keys()):
        if color.get(node, WHITE) == WHITE:
            found = visit(node)
            if found is not None:
                return found
    return None


def _detect_cycles(
    dependencies: dict[int, dict[str, int]],
    endpoints: Sequence[EndpointDependencyInput],
) -> tuple[set[tuple[int, str]], tuple[PlaywrightGenerationWarning, ...]]:
    # Repete a busca até o grafo (ainda não modificado de verdade aqui — só
    # uma cópia local, ver `working`) ficar acíclico: cada iteração remove
    # o ciclo INTEIRO que encontrou, nunca uma aresta isolada, e pode haver
    # mais de um ciclo disjunto na mesma suíte.
    working: dict[int, dict[str, int]] = {
        consumer_index: dict(by_variable) for consumer_index, by_variable in dependencies.items()
    }
    cyclic_edges: set[tuple[int, str]] = set()

    while True:
        cycle_edges = _find_one_cycle(_consumers_of(working))
        if not cycle_edges:
            break
        for consumer_index, variable_name in cycle_edges:
            cyclic_edges.add((consumer_index, variable_name))
            del working[consumer_index][variable_name]
            if not working[consumer_index]:
                del working[consumer_index]

    warnings = tuple(
        PlaywrightGenerationWarning(
            code=CIRCULAR_VARIABLE_DEPENDENCY,
            message=(
                f"Variável '{variable_name}' formaria uma dependência circular entre "
                f"'{endpoints[consumer_index].endpoint_source}' e "
                f"'{endpoints[dependencies[consumer_index][variable_name]].endpoint_source}'; "
                "nenhum dos dois foi ligado como produtor/consumidor um do outro."
            ),
            endpoint=endpoints[consumer_index].endpoint_source,
            scenario=None,
            location=_LOCATION_PATH,
            metadata=(("variable", variable_name),),
        )
        for consumer_index, variable_name in sorted(cyclic_edges)
    )
    return cyclic_edges, warnings


def _topological_order(count: int, dependencies: dict[int, dict[str, int]]) -> tuple[int, ...]:
    # Kahn, processando sempre o menor índice disponível — preserva a
    # ordem original entre nós independentes (nunca embaralha quem não
    # tem relação nenhuma), determinístico por construção (nunca depende
    # de ordem de iteração de um set/dict).
    required_by: dict[int, set[int]] = {index: set() for index in range(count)}
    for consumer_index, by_variable in dependencies.items():
        required_by[consumer_index].update(by_variable.values())

    remaining = dict(required_by)
    ordered: list[int] = []
    available = sorted(index for index, deps in remaining.items() if not deps)
    placed: set[int] = set()

    while available:
        current = available.pop(0)
        ordered.append(current)
        placed.add(current)
        del remaining[current]
        newly_available = []
        for index, deps in remaining.items():
            if current in deps:
                deps.discard(current)
                if not deps and index not in placed:
                    newly_available.append(index)
        available.extend(newly_available)
        available.sort()

    # Nunca deveria sobrar nada (ciclos já foram removidos antes de
    # chamar isto) — se sobrar por algum motivo defensivo, os índices
    # restantes entram no fim, na ordem original, nunca descartados.
    if remaining:
        ordered.extend(sorted(remaining.keys()))

    return tuple(ordered)


def _build_linked_endpoints(
    endpoints: Sequence[EndpointDependencyInput],
    dependencies: dict[int, dict[str, int]],
) -> tuple[LinkedEndpoint, ...]:
    usages_by_consumer: dict[int, list[VariableUsage]] = {}
    claimed_by_producer: dict[int, set[str]] = {}
    for consumer_index, by_variable in dependencies.items():
        for variable_name, producer_index in by_variable.items():
            producer_test_id = producer_test_id_for(endpoints[producer_index].endpoint_source)
            usages_by_consumer.setdefault(consumer_index, []).append(
                VariableUsage(
                    variable_name=variable_name,
                    producer_test_id=producer_test_id,
                    location=_LOCATION_PATH,
                )
            )
            claimed_by_producer.setdefault(producer_index, set()).add(variable_name)

    return tuple(
        LinkedEndpoint(
            variable_usages=tuple(usages_by_consumer.get(index, ())),
            claimed_extraction_names=frozenset(claimed_by_producer.get(index, ())),
        )
        for index in range(len(endpoints))
    )
