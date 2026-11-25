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
    args = parser.parse_args()
    if args.action == "catalog":
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
