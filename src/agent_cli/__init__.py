"""One uniform CLI contract for tools whose main caller is an LLM agent.

These tools are invoked by an agent ~99.9% of the time, and an agent probing a
tool it has not read behaves differently from a human: it reads **stdout**, it
branches on the **exit code**, and it will not scroll back to find a usage stub
on stderr. So the contract is:

* **A bare invocation prints FULL help to stdout and exits 0** — not argparse's
  usage error on stderr with exit 2. This is the load-bearing one. It means an
  agent's first blind call at a tool returns the whole surface, on the stream it
  is reading, on the first try.
* **``--version`` carries the script's on-disk path**, so an agent can go read
  the source of the thing it just ran.
* **Errors are JSON on stdout with a non-zero exit** (:func:`die`), because
  "I couldn't" must parse the way success does. A caller doing
  ``json.loads(stdout)`` should not need a second parser for the failure path.
* **An unhandled exception becomes one of those JSON errors** (:func:`wrap_main`),
  since the one failure that most needs to be machine-readable is the one nobody
  anticipated.

Because the behaviour lives in one place, a lint or health check can assert it
structurally — "does this tool build its parser through the factory?" — rather
than re-deriving the contract per tool. That is the point: the same six lines
hand-written across N tools is N chances to drift, and drift here is invisible
(a tool that exits 1 after printing correct help looks fine to a human and
reads as a failure to every caller).

Prior art: a shared CLI base is the Python analog of .NET's ``System.CommandLine``
``RootCommand``, or a ``CliHostBase`` — one factory owns prog/version/help so N
tools cannot each re-implement and drift on the boilerplate. Click's
``@click.group`` occupies similar ground; this stays on argparse and the stdlib
so a single-file PEP-723 script can depend on it without pulling a tree.

Deliberately NOT adopted: a Levenshtein "did you mean?" suggestion on an unknown
subcommand. That helps a human typing; an LLM caller re-reads the help instead,
so it is surface area with no consumer.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

__all__ = [
    "add_version_argument",
    "build_parser",
    "die",
    "parse_args_or_help",
    "wrap_main",
]

__version__ = "0.1.0"


class _VersionAction(argparse.Action):
    """``--version`` that prints its string VERBATIM to stdout, then exits 0.

    argparse's built-in ``version`` action routes the string through the
    HelpFormatter, which line-*wraps* it to the terminal width. That mangles a
    version carrying a full on-disk path: the path breaks across several lines,
    which is ugly for a human and unparseable for the machine caller these tools
    mostly serve. Printing verbatim keeps ``--version`` a single clean line.
    """

    def __init__(
        self,
        option_strings: list[str],
        version: str,
        dest: str = argparse.SUPPRESS,
        default: str = argparse.SUPPRESS,
        help: str = "show program's version number and exit",  # noqa: A002
    ) -> None:
        super().__init__(
            option_strings=option_strings,
            dest=dest,
            default=default,
            nargs=0,
            help=help,
        )
        self.version = version

    def __call__(self, parser, namespace, values, option_string=None) -> None:  # noqa: ANN001
        sys.stdout.write(self.version + "\n")
        parser.exit()


def add_version_argument(
    parser: argparse.ArgumentParser,
    source_file: str | None = None,
    *,
    provenance: str | None = None,
) -> None:
    """Wire ``--version`` on *parser*.

    *source_file* — pass the client's ``__file__``. The resolved on-disk path is
    appended in ``[...]``, so ``--version`` also answers "where is this tool?".
    A single-shot client that does not use :func:`build_parser` can call this
    directly to get the same contract.

    *provenance* — an opaque string identifying the build this tool was deployed
    from: a commit SHA, a release number, whatever proves it is the code you
    think it is. **Injected rather than baked**, because how a deployment stamps
    itself is specific to that deployment; a shared package cannot know it.

    When *provenance* is omitted the version line simply does not carry one.
    It is NOT filled in with ``"unknown"`` — a tool that never had provenance and
    one whose provenance could not be read would then look identical, and a
    confident-looking answer nobody can act on is worse than a short one.

    The string is built from ``parser.prog`` directly rather than ``%(prog)s``,
    so the verbatim :class:`_VersionAction` needs no ``%`` interpolation and a
    ``%`` in a path cannot break the output.
    """
    parts = [parser.prog]
    if provenance:
        parts.append(provenance)
    if source_file is not None:
        parts.append(f"[{Path(source_file).resolve()}]")
    parser.add_argument("--version", action=_VersionAction, version=" ".join(parts))


def build_parser(
    source_file: str,
    *,
    description: str | None = None,
    provenance: str | None = None,
    **kwargs: Any,  # noqa: ANN401 — passed straight through to ArgumentParser
) -> tuple[argparse.ArgumentParser, argparse._SubParsersAction]:
    """Build a subcommand tool's top-level parser + subparsers, uniformly.

    Pass the client's ``__file__`` as *source_file*: it sets ``prog`` to the
    script's basename and threads the on-disk path into ``--version``. Returns
    ``(parser, subparsers)``; add subcommands to *subparsers* as usual, then
    parse with :func:`parse_args_or_help`.

    Subparsers are created with ``dest="command"`` and deliberately WITHOUT
    ``required=True``. That is the whole trick: ``required=True`` makes argparse
    reject a bare call with a usage stub on stderr and exit 2, which is the
    behaviour this package exists to prevent. The "you must pick a subcommand"
    check moves into :func:`parse_args_or_help`, which sits before the client's
    dispatch and so works with if/elif, ``args.func``, or a dict.
    """
    parser = argparse.ArgumentParser(prog=Path(source_file).name, description=description, **kwargs)
    add_version_argument(parser, source_file, provenance=provenance)
    subparsers = parser.add_subparsers(dest="command")
    return parser, subparsers


def parse_args_or_help(
    parser: argparse.ArgumentParser, argv: list[str] | None = None
) -> argparse.Namespace:
    """Parse args; on a bare call print FULL help to stdout and exit 0.

    Replaces a plain ``parser.parse_args()`` in a tool's ``main()``. Because
    :func:`build_parser` leaves subparsers non-required, a missing subcommand
    lands here rather than as an argparse error — so the agent-friendly
    "bare -> full help, exit 0" behaviour is guaranteed by the factory instead of
    being re-implemented, slightly differently, in every tool.
    """
    args = parser.parse_args(argv)
    if getattr(args, "command", None) is None:
        parser.print_help()
        raise SystemExit(0)
    return args


def die(message: str, **extra: Any) -> NoReturn:  # noqa: ANN401 — arbitrary JSON-serialisable context is the point
    """Fail as JSON on STDOUT with a non-zero exit.

    "I couldn't" must parse the way success does: a caller doing
    ``result = json.loads(stdout)`` should not need a second parser for the
    failure path, so the error JSON lands on the SAME stream a success payload
    would. Branch on the exit code, not on which stream had output.

    ``**extra`` merges in alongside ``error`` for structured context, e.g.
    ``die("not found", hint="run `list` to see valid ids")``.
    """
    print(json.dumps({"error": message, **extra}))
    sys.exit(1)


def wrap_main(fn: Callable[[], object]) -> NoReturn:
    """Run *fn*; convert an UNHANDLED exception into a :func:`die` call.

    A tool's handled error paths usually go through something ``die()``-shaped
    already, but nothing catches the unexpected case — an unforeseen bug still
    surfaces as a raw traceback on stderr, breaking the "errors are JSON too"
    contract for the one class of failure that most needs it. Call from the
    ``__main__`` guard::

        if __name__ == "__main__":
            wrap_main(main)

    ``KeyboardInterrupt`` and ``SystemExit`` pass through untouched — those are
    deliberate exits (including :func:`die` itself), not bugs. The traceback is
    preserved in the JSON rather than dropped, so the failure stays debuggable
    without having to reproduce it live.

    *fn*'s return value still decides the exit code where the tool follows the
    ``sys.exit(main())`` convention: an ``int`` is passed straight to
    :func:`sys.exit`, and ``None`` exits 0.
    """
    try:
        result = fn()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — this IS the catch-all boundary
        die(str(exc) or type(exc).__name__, traceback=traceback.format_exc()[-2000:])
    sys.exit(result if isinstance(result, int) else 0)
