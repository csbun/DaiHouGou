import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daihougou-poc")
    commands = parser.add_subparsers(dest="group", required=True)
    for name in ("inventory", "camera", "speaker", "report"):
        commands.add_parser(name)
    return parser


def main() -> None:
    build_parser().parse_args()
