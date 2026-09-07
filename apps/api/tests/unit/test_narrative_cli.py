"""The narrative stages as `corpus-steward` subcommands.

The exit code is the contract worth testing. A pipeline reacting to "source analysis
refused this artifact" must not have to know whether the source was a FHIR package or a
guideline PDF, so the narrative commands reuse the status of the stage class they belong
to rather than minting new ones - 5 for source analysis, 7 for materialization.
"""

import argparse
import inspect

import pytest

from app.corpus_steward import cli
from app.corpus_steward.cli import build_parser


def _commands() -> dict[str, argparse.ArgumentParser]:
    parser = build_parser()
    return parser._subparsers._group_actions[0].choices


@pytest.mark.parametrize("command", ["analyze-narrative", "materialize-narrative"])
def test_the_narrative_commands_are_registered(command: str) -> None:
    assert command in _commands()


@pytest.mark.parametrize(
    ("command", "handler"),
    [
        ("analyze-narrative", "analyze_narrative"),
        ("materialize-narrative", "materialize_narrative"),
    ],
)
def test_each_command_dispatches_to_a_handler(command: str, handler: str) -> None:
    assert inspect.iscoroutinefunction(getattr(cli, handler))
    source = inspect.getsource(cli.main)
    assert f'arguments.command == "{command}"' in source
    assert f"{handler}(arguments)" in source


@pytest.mark.parametrize("command", ["analyze-narrative", "materialize-narrative"])
def test_each_command_takes_a_candidate_and_an_optional_item(command: str) -> None:
    """`--item-id` is what makes the multi-item topology drivable at all.

    A WHO NCD candidate carries thirteen guidelines; without it only the first is
    reachable from the command line.
    """

    parsed = build_parser().parse_args([command, "RC_123456", "--item-id", "GUIDE_2"])

    assert parsed.command == command
    assert parsed.candidate_id == "RC_123456"
    assert parsed.item_id == "GUIDE_2"
    assert parsed.output is None

    without_item = build_parser().parse_args([command, "RC_123456"])
    assert without_item.item_id is None


@pytest.mark.parametrize(
    ("handler", "status"),
    [("analyze_narrative", 5), ("materialize_narrative", 7)],
)
def test_a_blocked_stage_exits_with_its_stage_class_status(handler: str, status: int) -> None:
    """Exit codes name the stage, not the topology.

    `analyze-narrative` shares 5 with `process-structured` and `materialize-narrative`
    shares 7 with `materialize`, so a signed policy block stays distinguishable from a
    crash without a caller having to learn a second vocabulary.
    """

    source = inspect.getsource(getattr(cli, handler))
    assert f"return {status} if result.state is" in source


@pytest.mark.parametrize("command", ["analyze-narrative", "materialize-narrative"])
def test_each_command_can_sign_and_reach_the_artifact_store(command: str) -> None:
    parsed = build_parser().parse_args([command, "RC_123456"])

    assert hasattr(parsed, "database_url")
    assert hasattr(parsed, "artifact_store")
    assert hasattr(parsed, "signing_key")
