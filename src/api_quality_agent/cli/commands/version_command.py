import argparse

from api_quality_agent import __version__


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = subparsers.add_parser("version", help="Exibe a versão da aplicação.")
    parser.set_defaults(handler=_handle_version)


def _handle_version(_args: argparse.Namespace) -> int:
    print(f"API Quality Orchestrator {__version__}")
    return 0
