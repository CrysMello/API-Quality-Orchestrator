"""Caracterização do inventário de comandos/flags da CLI, exatamente como
registrados hoje em `cli.main.build_parser()`.

`--target` em `generate` foi adicionado deliberadamente na Parte 04 do plano
Playwright (Bloco 2) — a asserção de `generate` já reflete esse estado.
`run` ainda vai ganhar `--engine`/`--playwright-dir` na Fase 8, o que exigirá
uma atualização consciente análoga.

Se este teste quebrar fora de uma dessas mudanças planejadas, é regressão
real (comando/flag removido ou renomeado sem querer).
"""

import argparse

from api_quality_agent.cli.main import build_parser


def _subparser_actions(
    parser: argparse.ArgumentParser,
) -> dict[str, argparse.ArgumentParser]:
    subparsers_action = next(
        action
        for action in parser._actions  # noqa: SLF001 - único jeito de introspectar argparse
        if isinstance(action, argparse._SubParsersAction)
    )
    return dict(subparsers_action.choices)


def _option_strings(sub: argparse.ArgumentParser) -> set[str]:
    options: set[str] = set()
    for action in sub._actions:  # noqa: SLF001
        options.update(action.option_strings)
    return options


def test_registered_top_level_commands_are_unchanged() -> None:
    subcommands = _subparser_actions(build_parser())

    assert set(subcommands) == {
        "config",
        "doctor",
        "workspace",
        "list",
        "generate",
        "update",
        "run",
        "report",
        "version",
    }


def test_generate_command_flags_are_unchanged() -> None:
    subcommands = _subparser_actions(build_parser())

    assert _option_strings(subcommands["generate"]) == {
        "-h",
        "--help",
        "-c",
        "--collection-id",
        "-n",
        "--collection-name",
        "-f",
        "--file",
        "--openapi-file",
        "--contract-file",
        "--collection-path-prefix",
        "--strict-contract-match",
        "-y",
        "--yes",
        "--target",
    }


def test_run_command_flags_are_unchanged() -> None:
    subcommands = _subparser_actions(build_parser())

    assert _option_strings(subcommands["run"]) == {
        "-h",
        "--help",
        "-c",
        "--collection-id",
        "-n",
        "--collection-name",
        "-f",
        "--file",
        "--newman-executable",
        "-e",
        "--environment",
    }


def test_report_command_flags_are_unchanged() -> None:
    subcommands = _subparser_actions(build_parser())

    assert _option_strings(subcommands["report"]) == {
        "-h",
        "--help",
        "-i",
        "--input",
        "-o",
        "--output",
        "--format",
        "--overwrite",
    }
