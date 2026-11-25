"""Function discovery and deterministic documentation generation."""

import argparse
import json

from . import registry


def main():
    parser = argparse.ArgumentParser(prog="python -m quant")
    commands = parser.add_subparsers(dest="action", required=True)
    inventory = commands.add_parser("functions")
    inventory.add_argument("--category")
    commands.add_parser("describe").add_argument("name")
    commands.add_parser("search").add_argument("query")
    generate = commands.add_parser("catalog")
    generate.add_argument("--out", default="docs/FUNCTION_CATALOG.json")
    generate.add_argument("--markdown", default="docs/API_REFERENCE.md")
    for name in (
        "research-status",
        "research-submit",
        "research-run",
        "research-cancel",
    ):
        command = commands.add_parser(name)
        command.add_argument("database")
        if name == "research-submit":
            command.add_argument("specification")
        elif name == "research-cancel":
            command.add_argument("job_id")
        elif name == "research-run":
            command.add_argument(
                "--handler",
                action="append",
                required=True,
                help="Trusted local handler as kind=module:function",
            )
            command.add_argument("--claim-size", type=int, default=64)
    args = parser.parse_args()
    if args.action.startswith("research-"):
        from importlib import import_module
        from pathlib import Path
        from . import research

        if args.action == "research-status":
            value = research.status(args.database)
        elif args.action == "research-submit":
            payload = json.loads(Path(args.specification).read_text(encoding="utf-8"))
            value = research.submit(args.database, **payload)
        elif args.action == "research-cancel":
            research.cancel(args.database, args.job_id)
            value = research.status(args.database, ids=[args.job_id])
        else:
            handlers = {}
            for binding in args.handler:
                kind, reference = binding.split("=", 1)
                module, function = reference.split(":", 1)
                handlers[kind] = getattr(import_module(module), function)
            value = research.run(args.database, handlers, claim_size=args.claim_size)
        print(json.dumps(value, indent=2, default=str))
    elif args.action == "catalog":
        registry.generate_catalog(args.out)
        registry.generate_api(args.markdown)
    elif args.action == "describe":
        try:
            print(json.dumps(registry.describe(args.name), indent=2))
        except KeyError:
            parser.error(f"Unknown SDK function: {args.name}")
    else:
        names = (
            registry.search(args.query)
            if args.action == "search"
            else registry.list_by_category(args.category)
            if args.category
            else registry.list_functions()
        )
        print("\n".join(names))


if __name__ == "__main__":
    main()
