import argparse

import pytest

from api_quality_agent import __version__
from api_quality_agent.cli.main import _dispatch, main
from api_quality_agent.domain.exceptions import ResourceNotFoundError


def test_help_exits_with_zero_and_prints_usage(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "api-quality-orchestrator" in captured.out


def test_version_exits_with_zero_and_prints_version(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert __version__ in captured.out


def test_missing_command_exits_with_invalid_input_code():
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 2


def test_dispatch_maps_domain_exception_to_exit_code(capsys):
    def handler(_args: argparse.Namespace) -> int:
        raise ResourceNotFoundError("recurso não encontrado")

    exit_code = _dispatch(argparse.Namespace(handler=handler))

    assert exit_code == 4
    captured = capsys.readouterr()
    assert "recurso não encontrado" in captured.err


def test_dispatch_maps_unexpected_exception_to_internal_failure(capsys):
    def handler(_args: argparse.Namespace) -> int:
        raise RuntimeError("bug inesperado")

    exit_code = _dispatch(argparse.Namespace(handler=handler))

    assert exit_code == 8
    captured = capsys.readouterr()
    assert "bug inesperado" in captured.err


def test_dispatch_returns_handler_result_on_success():
    def handler(_args: argparse.Namespace) -> int:
        return 0

    assert _dispatch(argparse.Namespace(handler=handler)) == 0
