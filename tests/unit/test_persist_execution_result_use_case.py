import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from api_quality_agent.application.use_cases import PersistExecutionResultUseCase
from api_quality_agent.domain.models import (
    ExecutionResult,
    ExecutionResultLocation,
    HttpTransaction,
    HttpTransactionHeader,
    InfrastructureFailure,
    InfrastructureFailureType,
    TestFailure,
    TraceArtifact,
)

_STARTED_AT = datetime(2026, 7, 20, 10, 35, 12, tzinfo=timezone.utc)
_FINISHED_AT = datetime(2026, 7, 20, 10, 35, 46, tzinfo=timezone.utc)


class _CapturingRepository:
    def __init__(self) -> None:
        self.captured_content: str | None = None

    def save(self, *, content: str) -> ExecutionResultLocation:
        self.captured_content = content
        return ExecutionResultLocation(path="artifacts/run_fake/result.json")


def _success_result(**overrides) -> ExecutionResult:
    defaults = dict(
        collection_source="/tmp/whatever.json",
        success=True,
        exit_code=0,
        duration_seconds=34.1,
        total_requests=28,
        total_assertions=312,
        failed_assertions=3,
        test_failures=(
            TestFailure(request_name="Criar pet", test_name="Status 201", error_message="boom"),
        ),
        infrastructure_failure=None,
        stdout="stdout sensível que não deve ser persistido",
        stderr="stderr sensível que não deve ser persistido",
    )
    defaults.update(overrides)
    return ExecutionResult(**defaults)


def test_persisted_json_has_expected_structure():
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result()

    location = use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        workspace_id="ws-1",
        workspace_name="QA Workspace",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    assert location.path == "artifacts/run_fake/result.json"
    payload = json.loads(repository.captured_content)
    assert payload == {
        "schema_version": "1.7",
        "execution": {
            "started_at": _STARTED_AT.isoformat(),
            "finished_at": _FINISHED_AT.isoformat(),
            "duration_seconds": 34.1,
        },
        "workspace": {"id": "ws-1", "name": "QA Workspace"},
        "collection": {"id": "col-1", "name": "PetStore"},
        "summary": {
            "requests": 28,
            "assertions": 312,
            "passed": 309,
            "failed": 3,
            "skipped": 0,
        },
        "test_failures": [
            {"request_name": "Criar pet", "test_name": "Status 201", "error_message": "boom"},
        ],
        "http_transactions": [],
        "assertion_results": [],
        "trace_artifacts": [],
        "evidence_failures": [],
        "success": True,
        "infrastructure_failure": None,
    }


# --- P1.1: persistência de skipped_tests ----------------------------------------------------


def test_skipped_tests_is_persisted_correctly():
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result(skipped_tests=4)

    use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(repository.captured_content)
    assert payload["summary"]["skipped"] == 4


def test_skipped_tests_zero_is_persisted_correctly():
    # skipped_tests=0 é o default de ExecutionResult (Newman nunca tem
    # skipped) — precisa ser persistido como 0 explícito, nunca omitido.
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result(skipped_tests=0)

    use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(repository.captured_content)
    assert payload["summary"]["skipped"] == 0
    assert "skipped" in payload["summary"]


def test_skipped_tests_does_not_affect_other_summary_fields():
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result(skipped_tests=7)

    use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(repository.captured_content)
    assert payload["summary"]["requests"] == 28
    assert payload["summary"]["assertions"] == 312
    assert payload["summary"]["passed"] == 309
    assert payload["summary"]["failed"] == 3


# --- P1.2: persistência de http_transactions --------------------------------------------


def _transaction(**overrides) -> HttpTransaction:
    defaults = dict(
        method="GET",
        url="https://api.exemplo.com/users",
        request_headers=(HttpTransactionHeader(name="Accept", value="application/json"),),
        request_body=None,
        response_status=200,
        response_headers=(HttpTransactionHeader(name="content-type", value="application/json"),),
        response_body='{"items": []}',
    )
    defaults.update(overrides)
    return HttpTransaction(**defaults)


def test_http_transactions_is_persisted_correctly():
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result(http_transactions=(_transaction(),))

    use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(repository.captured_content)
    assert payload["http_transactions"] == [
        {
            "test_id": "",
            "method": "GET",
            "url": "https://api.exemplo.com/users",
            "request_headers": {"Accept": "application/json"},
            "request_body": None,
            "response_status": 200,
            "response_headers": {"content-type": "application/json"},
            "response_body": '{"items": []}',
        }
    ]


def test_http_transactions_empty_is_persisted_as_empty_list():
    # http_transactions=() é o default de ExecutionResult (Newman nunca
    # preenche isto) — precisa ser persistido como [] explícito, nunca
    # omitido.
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result(http_transactions=())

    use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(repository.captured_content)
    assert payload["http_transactions"] == []
    assert "http_transactions" in payload


def test_multiple_http_transactions_are_persisted_in_order():
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result(
        http_transactions=(
            _transaction(method="GET"),
            _transaction(method="POST", request_body='{"name": "Maria"}', response_status=201),
        )
    )

    use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(repository.captured_content)
    assert [t["method"] for t in payload["http_transactions"]] == ["GET", "POST"]


def test_persisted_json_never_contains_a_secret_present_in_a_transaction_header_or_body():
    # A garantia de "nenhum secret conhecido na evidência persistida" já é
    # responsabilidade do PlaywrightAdapter (mascara ANTES de montar o
    # ExecutionResult) — este teste prova que a camada de persistência em
    # si não reintroduz o valor bruto por engano (ex.: bug de serialização
    # usando um campo errado).
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result(
        http_transactions=(
            _transaction(
                request_headers=(
                    HttpTransactionHeader(name="Authorization", value="Bearer sk_l****3456"),
                ),
                request_body='{"password": "sk_l****3456"}',
            ),
        )
    )

    use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    assert "sk_live_super_secret_token_123456" not in repository.captured_content


def test_persisted_json_workspace_is_null_when_not_provided():
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result()

    use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(repository.captured_content)
    assert payload["workspace"] == {"id": None, "name": None}


def test_infrastructure_failure_is_serialized_as_structured_object():
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result(
        success=False,
        total_requests=0,
        total_assertions=0,
        failed_assertions=0,
        test_failures=(),
        infrastructure_failure=InfrastructureFailure(
            failure_type=InfrastructureFailureType.EXECUTABLE_NOT_FOUND,
            message="Executável do Newman não encontrado.",
        ),
    )

    use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(repository.captured_content)
    assert payload["infrastructure_failure"] == {
        "type": "executable_not_found",
        "message": "Executável do Newman não encontrado.",
    }


@pytest.mark.parametrize("field", ["execution", "collection", "summary"])
def test_persisted_json_never_contains_raw_stdout_or_stderr(field):
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result()

    use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    assert "stdout" not in repository.captured_content
    assert "stderr" not in repository.captured_content
    assert "sensível" not in repository.captured_content


def test_persisted_json_never_contains_the_full_collection_document():
    repository = _CapturingRepository()
    use_case = PersistExecutionResultUseCase(repository)
    result = _success_result()

    use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(repository.captured_content)
    assert set(payload.keys()) == {
        "schema_version",
        "execution",
        "workspace",
        "collection",
        "summary",
        "test_failures",
        "http_transactions",
        "assertion_results",
        "trace_artifacts",
        "evidence_failures",
        "success",
        "infrastructure_failure",
    }


# --- P1.4 (hardening): movimentação do trace falhando -----------------------


class _RealFileRepository:
    # Diferente de _CapturingRepository (path fake "artifacts/run_fake/..."
    # nunca escrito de verdade) — este grava um result.json REAL num
    # diretório temporário real, necessário aqui porque
    # PersistExecutionResultUseCase._move_trace_artifacts resolve o
    # diretório de destino a partir de Path(location.path).resolve().parent
    # e de fato tenta mover arquivos físicos.
    def __init__(self, run_dir: Path) -> None:
        self._run_dir = run_dir

    def save(self, *, content: str) -> ExecutionResultLocation:
        self._run_dir.mkdir(parents=True, exist_ok=True)
        path = self._run_dir / "result.json"
        path.write_text(content, encoding="utf-8")
        return ExecutionResultLocation(path=str(path))


def test_trace_move_failure_never_creates_a_false_reference(tmp_path, monkeypatch):
    # Item 12/18: se shutil.move falhar, result.json NUNCA pode afirmar
    # que um arquivo existe em traces/ quando ele não foi de fato movido
    # pra lá.
    run_dir = tmp_path / "run_x"
    source_path = tmp_path / "masked_trace.zip"
    source_path.write_bytes(b"fake masked trace content")
    result = _success_result(
        trace_artifacts=(
            TraceArtifact(type="playwright-trace", test_id="test_x_fail", path=str(source_path)),
        )
    )
    use_case = PersistExecutionResultUseCase(_RealFileRepository(run_dir))

    def _failing_move(*args, **kwargs):
        raise OSError("simulated move failure")

    monkeypatch.setattr(shutil, "move", _failing_move)

    location = use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(Path(location.path).read_text(encoding="utf-8"))
    assert payload["trace_artifacts"] == []
    # Evidência preservada: o arquivo de origem nunca foi apagado (nunca
    # remove um artefato que não pôde ser movido, ver _move_trace_artifacts).
    assert source_path.is_file()
    # P1.5: a falha em si vira uma InfrastructureFailure explícita — nunca
    # só uma ausência silenciosa em trace_artifacts.
    assert len(payload["evidence_failures"]) == 1
    failure = payload["evidence_failures"][0]
    assert failure["type"] == "evidence_persistence_failed"
    assert failure["source"] == "playwright_trace"
    assert failure["test_id"] == "test_x_fail"
    assert "mover" in failure["message"].lower()
    # test failure funcional (já em _success_result) continua intacto.
    assert payload["success"] is True


def test_trace_move_partial_failure_only_references_the_ones_that_succeeded(
    tmp_path, monkeypatch
):
    # Dois traces: um se move com sucesso, o outro falha — result.json
    # nunca referencia o que falhou, mas continua referenciando o que deu
    # certo (preserva o restante do ExecutionResult quando possível).
    run_dir = tmp_path / "run_x"
    ok_source = tmp_path / "ok.zip"
    ok_source.write_bytes(b"ok")
    bad_source = tmp_path / "bad.zip"
    bad_source.write_bytes(b"bad")
    result = _success_result(
        trace_artifacts=(
            TraceArtifact(type="playwright-trace", test_id="test_ok_fail", path=str(ok_source)),
            TraceArtifact(type="playwright-trace", test_id="test_bad_fail", path=str(bad_source)),
        )
    )
    use_case = PersistExecutionResultUseCase(_RealFileRepository(run_dir))

    real_move = shutil.move

    def _selective_failing_move(src, dst, *args, **kwargs):
        if "bad" in str(src):
            raise OSError("simulated move failure")
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "move", _selective_failing_move)

    location = use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(Path(location.path).read_text(encoding="utf-8"))
    test_ids = {artifact["test_id"] for artifact in payload["trace_artifacts"]}
    assert test_ids == {"test_ok_fail"}
    moved_path = run_dir / payload["trace_artifacts"][0]["path"]
    assert moved_path.is_file()
    assert bad_source.is_file()  # nunca apagado, preservado para diagnóstico
    # P1.5: só o que falhou vira InfrastructureFailure — o que teve sucesso
    # nunca ganha uma falha inventada.
    assert len(payload["evidence_failures"]) == 1
    assert payload["evidence_failures"][0]["test_id"] == "test_bad_fail"


def test_trace_directory_creation_failure_produces_an_evidence_failure_for_every_artifact(
    tmp_path, monkeypatch
):
    # Item 5: falha ao CRIAR o diretório de destino (traces/) — nenhum dos
    # traces pode ser movido, cada um ganha sua própria InfrastructureFailure
    # correlacionada por test_id (nunca uma falha agregada sem test_id).
    run_dir = tmp_path / "run_x"
    source_a = tmp_path / "a.zip"
    source_a.write_bytes(b"a")
    source_b = tmp_path / "b.zip"
    source_b.write_bytes(b"b")
    result = _success_result(
        trace_artifacts=(
            TraceArtifact(type="playwright-trace", test_id="test_a_fail", path=str(source_a)),
            TraceArtifact(type="playwright-trace", test_id="test_b_fail", path=str(source_b)),
        )
    )
    use_case = PersistExecutionResultUseCase(_RealFileRepository(run_dir))

    real_mkdir = Path.mkdir

    def _selective_failing_mkdir(self, *args, **kwargs):
        # Só falha para o diretório "traces/" em si — o mkdir do próprio
        # run_dir (feito por _RealFileRepository.save(), ANTES de
        # _move_trace_artifacts entrar em ação) precisa continuar
        # funcionando normalmente, senão o teste nem chegaria a persistir
        # result.json.
        if self.name == "traces":
            raise OSError("simulated mkdir failure")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", _selective_failing_mkdir)

    location = use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(Path(location.path).read_text(encoding="utf-8"))
    assert payload["trace_artifacts"] == []
    assert len(payload["evidence_failures"]) == 2
    test_ids = {failure["test_id"] for failure in payload["evidence_failures"]}
    assert test_ids == {"test_a_fail", "test_b_fail"}
    for failure in payload["evidence_failures"]:
        assert failure["type"] == "evidence_persistence_failed"
        assert failure["source"] == "playwright_trace"
        assert "diretório" in failure["message"].lower()
    # Nenhum arquivo bruto foi apagado (evidência preservada).
    assert source_a.is_file()
    assert source_b.is_file()


def test_trace_move_success_removes_the_temporary_source_file(tmp_path):
    # Caminho feliz: depois de mover com sucesso, o arquivo temporário de
    # origem não sobra (nem ele, nem o diretório que só existia pra
    # hospedá-lo).
    run_dir = tmp_path / "run_x"
    source_dir = tmp_path / "masked_dir"
    source_dir.mkdir()
    source_path = source_dir / "trace.zip"
    source_path.write_bytes(b"masked content")
    result = _success_result(
        trace_artifacts=(
            TraceArtifact(type="playwright-trace", test_id="test_x_fail", path=str(source_path)),
        )
    )
    use_case = PersistExecutionResultUseCase(_RealFileRepository(run_dir))

    location = use_case.execute(
        result,
        collection_id="col-1",
        collection_name="PetStore",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )

    payload = json.loads(Path(location.path).read_text(encoding="utf-8"))
    assert len(payload["trace_artifacts"]) == 1
    persisted_path = run_dir / payload["trace_artifacts"][0]["path"]
    assert persisted_path.is_file()
    assert not source_path.exists()
    assert not source_dir.exists()  # diretório temporário vazio, removido
