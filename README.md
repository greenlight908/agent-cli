# agent-cli

One uniform CLI contract for tools whose main caller is an **LLM agent**, not a human.

An agent probing a tool it has not read behaves differently from a person: it reads
**stdout**, it branches on the **exit code**, and it will not scroll back to find a usage
stub on stderr. `agent-cli` makes every tool honour that:

- **A bare invocation prints FULL help to stdout and exits 0** — not argparse's usage
  error on stderr with exit 2. The load-bearing one: an agent's first blind call returns
  the whole surface, on the stream it is reading, first try.
- **`--version` carries the script's on-disk path**, so an agent can go read the source of
  what it just ran.
- **Errors are JSON on stdout with a non-zero exit** (`die`) — "I couldn't" parses the way
  success does.
- **Unhandled exceptions become one of those JSON errors** (`wrap_main`) — the failure
  nobody anticipated is the one that most needs to be machine-readable.

Zero runtime dependencies, stdlib argparse only, so a single-file PEP-723 script can
depend on it without pulling in a tree.

## Use

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["agent-cli @ git+https://github.com/greenlight908/agent-cli.git@v0.1.0"]
# ///
from agent_cli import build_parser, die, parse_args_or_help, wrap_main


def main() -> int:
    parser, sub = build_parser(__file__, description="Widget tool")
    sub.add_parser("list", help="List widgets")
    args = parse_args_or_help(parser)

    if args.command == "list":
        print(json.dumps({"widgets": []}))
    return 0


if __name__ == "__main__":
    wrap_main(main)
```

```console
$ ./widget_cli.py            # full help on stdout
$ echo $?
0
```

## Provenance is injected, not baked

`build_parser(..., provenance="a1b2c3d")` stamps a build identifier into `--version`, so
deployed code can prove which commit it is. It is a parameter because how a deployment
stamps itself is specific to that deployment — a shared package cannot know it.

Omit it and the version line simply does not carry one. It is **not** filled in with
`"unknown"`: a tool that never had provenance and one whose provenance could not be read
would then look identical, and a confident-looking answer nobody can act on is worse than
a short one.

## Prior art

The Python analog of .NET's `System.CommandLine` `RootCommand`, or a `CliHostBase`: one
factory owns prog/version/help so N tools cannot each re-implement and drift on the
boilerplate. Click's `@click.group` occupies similar ground; this stays on argparse and
the stdlib for the zero-dependency property above.

Deliberately **not** adopted: a Levenshtein "did you mean?" on an unknown subcommand. That
helps a human typing; an LLM caller re-reads the help instead, so it is surface area with
no consumer.
