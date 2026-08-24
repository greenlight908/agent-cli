"""The contract, asserted directly.

The load-bearing property is behavioural — bare invocation prints help to stdout
and exits 0 — so it is asserted on the real parser, not on source text. Every
static proxy for it is wrong in some direction: a tool can call `print_help()`
and still exit 1, and a tool can satisfy the contract without using this factory
at all.
"""

from __future__ import annotations

import argparse
import json
import sys

import pytest

from agent_cli import (
    add_version_argument,
    build_parser,
    die,
    parse_args_or_help,
    wrap_main,
)


def _tool(**kwargs: object) -> tuple[argparse.ArgumentParser, argparse._SubParsersAction]:
    parser, sub = build_parser("/tmp/widget_cli.py", description="Widget tool", **kwargs)
    sub.add_parser("list", help="List widgets")
    return parser, sub


class TestBareInvocation:
    def test_prints_full_help_to_stdout_and_exits_zero(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        parser, _ = _tool()

        with pytest.raises(SystemExit) as exit_info:
            parse_args_or_help(parser, [])

        assert exit_info.value.code == 0, "a bare invocation must not read as a failure"
        captured = capsys.readouterr()
        assert "usage:" in captured.out
        assert "list" in captured.out, "printed help without the subcommand surface"
        assert captured.err == "", "help belongs on stdout — an agent does not read stderr"

    def test_the_subparser_is_not_required(self) -> None:
        """The whole trick. `required=True` is what makes argparse exit 2 to stderr,
        so leaving it off is what lets the bare call reach the help path at all."""
        parser, sub = _tool()

        assert sub.required is False

    def test_a_real_subcommand_still_parses(self) -> None:
        parser, _ = _tool()

        args = parse_args_or_help(parser, ["list"])

        assert args.command == "list"

    def test_an_unknown_subcommand_is_still_an_error(self) -> None:
        """Non-required must not mean permissive: a wrong command is a real mistake."""
        parser, _ = _tool()

        with pytest.raises(SystemExit) as exit_info:
            parse_args_or_help(parser, ["nope"])

        assert exit_info.value.code != 0


class TestVersion:
    def test_carries_the_on_disk_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        parser, _ = _tool()

        with pytest.raises(SystemExit):
            parser.parse_args(["--version"])

        assert "/tmp/widget_cli.py" in capsys.readouterr().out

    def test_carries_provenance_when_given(self, capsys: pytest.CaptureFixture[str]) -> None:
        parser, _ = _tool(provenance="a1b2c3d")

        with pytest.raises(SystemExit):
            parser.parse_args(["--version"])

        assert "a1b2c3d" in capsys.readouterr().out

    def test_omits_provenance_rather_than_inventing_unknown(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A tool that never had provenance and one whose provenance could not be
        read must not look identical."""
        parser, _ = _tool()

        with pytest.raises(SystemExit):
            parser.parse_args(["--version"])

        assert "unknown" not in capsys.readouterr().out.lower()

    def test_is_printed_verbatim_not_wrapped(self, capsys: pytest.CaptureFixture[str]) -> None:
        """argparse's built-in version action wraps to terminal width, which breaks
        a long path across lines — unparseable for the machine caller."""
        parser = argparse.ArgumentParser(prog="x")
        long_path = "/" + "d" * 200 + "/widget_cli.py"
        add_version_argument(parser, long_path, provenance="a1b2c3d")

        with pytest.raises(SystemExit):
            parser.parse_args(["--version"])

        out = capsys.readouterr().out
        assert out.count("\n") == 1, "version was wrapped across lines"

    def test_a_percent_in_the_path_does_not_break_the_output(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        parser = argparse.ArgumentParser(prog="x")
        add_version_argument(parser, "/tmp/100%_cli.py")

        with pytest.raises(SystemExit):
            parser.parse_args(["--version"])

        assert "100%" in capsys.readouterr().out


class TestErrorsAreJsonToo:
    def test_die_writes_json_to_stdout_and_exits_nonzero(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exit_info:
            die("nope", hint="try list")

        assert exit_info.value.code == 1
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload == {"error": "nope", "hint": "try list"}
        assert captured.err == "", "a caller doing json.loads(stdout) must not need a second parser"

    def test_wrap_main_turns_an_unhandled_exception_into_json(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def boom() -> int:
            raise RuntimeError("kaboom")

        with pytest.raises(SystemExit) as exit_info:
            wrap_main(boom)

        assert exit_info.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["error"] == "kaboom"
        assert "RuntimeError" in payload["traceback"], (
            "dropped the traceback, so the failure is undebuggable"
        )

    def test_wrap_main_names_the_exception_type_when_there_is_no_message(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def boom() -> int:
            raise ValueError

        with pytest.raises(SystemExit):
            wrap_main(boom)

        assert json.loads(capsys.readouterr().out)["error"] == "ValueError"

    def test_wrap_main_passes_deliberate_exits_through(self) -> None:
        """die() itself raises SystemExit; swallowing it would turn every intended
        failure into a nested error."""

        def quit_cleanly() -> int:
            raise SystemExit(3)

        with pytest.raises(SystemExit) as exit_info:
            wrap_main(quit_cleanly)

        assert exit_info.value.code == 3

    def test_wrap_main_passes_keyboard_interrupt_through(self) -> None:
        def interrupted() -> int:
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            wrap_main(interrupted)

    def test_wrap_main_uses_an_int_return_as_the_exit_code(self) -> None:
        with pytest.raises(SystemExit) as exit_info:
            wrap_main(lambda: 2)

        assert exit_info.value.code == 2

    def test_wrap_main_treats_none_as_success(self) -> None:
        with pytest.raises(SystemExit) as exit_info:
            wrap_main(lambda: None)

        assert exit_info.value.code == 0


def test_the_package_is_dependency_free() -> None:
    """The zero-dependency property is the reason a PEP-723 one-file script can use
    this. A stray import would break that silently, so it is pinned."""
    import agent_cli

    source = __import__("pathlib").Path(agent_cli.__file__).read_text(encoding="utf-8")
    for line in source.splitlines():
        if line.startswith(("import ", "from ")) and "agent_cli" not in line:
            module = line.split()[1].split(".")[0]
            assert module in sys.stdlib_module_names | {"__future__"}, (
                f"{module} is not stdlib — the zero-dependency promise is broken"
            )
