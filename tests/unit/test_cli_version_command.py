from api_quality_agent import __version__
from api_quality_agent.cli.main import main


def test_version_command_prints_version_and_succeeds(capsys):
    exit_code = main(["version"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert __version__ in captured.out
    assert "API Quality Orchestrator" in captured.out


def test_version_flag_on_root_parser_exits_zero(capsys):
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("--version deveria encerrar o processo via SystemExit")

    captured = capsys.readouterr()
    assert __version__ in captured.out
