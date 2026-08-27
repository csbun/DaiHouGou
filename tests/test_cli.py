from daihougou_poc.cli import build_parser


def test_cli_lists_required_command_groups() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    assert "inventory" in help_text
    assert "camera" in help_text
    assert "speaker" in help_text
    assert "report" in help_text
