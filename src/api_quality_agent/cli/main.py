import argparse
import sys
from collections.abc import Sequence

from api_quality_agent import __version__
from api_quality_agent.cli.commands import (
    config_command,
    doctor_command,
    generate_command,
    list_command,
    report_command,
    run_command,
    update_command,
    version_command,
    workspace_command,
)
from api_quality_agent.cli.exit_codes import resolve_exit_code
from api_quality_agent.domain.exceptions import ApiQualityAgentError

PROG_NAME = "api-quality-orchestrator"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG_NAME,
        description="Agente de automação de qualidade para APIs.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    config_command.register(subparsers)
    doctor_command.register(subparsers)
    workspace_command.register(subparsers)
    list_command.register(subparsers)
    generate_command.register(subparsers)
    update_command.register(subparsers)
    run_command.register(subparsers)
    report_command.register(subparsers)
    version_command.register(subparsers)

    return parser


def _dispatch(args: argparse.Namespace) -> int:
    try:
        return args.handler(args)
    except ApiQualityAgentError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return resolve_exit_code(exc)
    except Exception as exc:
        print(f"Erro inesperado: {exc}", file=sys.stderr)
        return resolve_exit_code(exc)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return _dispatch(args)


def _ensure_utf8_streams() -> None:
    # Em alguns terminais Windows (ex.: Git Bash/mintty), o processo herda uma
    # codepage que não é UTF-8, corrompendo a acentuação das mensagens.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def run() -> None:
    _ensure_utf8_streams()
    sys.exit(main())


if __name__ == "__main__":
    run()
