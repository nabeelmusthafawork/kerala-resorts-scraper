"""Tests for the `resorts help` command. Offline only."""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from resorts_scraper.cli import build_parser, help_text, main  # noqa: E402

COMMANDS = {
    "run",
    "emails",
    "phones",
    "status",
    "export",
    "clean",
    "rank",
    "enrich",
    "doctor",
    "help",
}


class TestHelp(unittest.TestCase):
    def test_parser_exposes_every_documented_command(self):
        _ap, parsers = build_parser()
        self.assertSetEqual(set(parsers), COMMANDS)

    def test_help_text_covers_every_command(self):
        text = help_text()
        for name in COMMANDS:
            self.assertIn(name, text, f"{name} missing from the help text")
        for section in ("Scrape", "Data", "Operations", "Examples", "Notes"):
            self.assertIn(section, text)

    def test_help_prints_the_grouped_list(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["help"])
        self.assertEqual(code, 0)
        out = buf.getvalue()
        self.assertIn("usage: resorts", out)
        self.assertIn("resorts help rank", out)

    def test_help_topic_prints_that_command_options(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["help", "rank"])
        self.assertEqual(code, 0)
        out = buf.getvalue()
        self.assertIn("resorts rank", out)
        self.assertIn("--prior", out)

    def test_help_for_help(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["help", "help"])
        self.assertEqual(code, 0)
        self.assertIn("topic", buf.getvalue())

    def test_help_unknown_topic_exits_two(self):
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["help", "nope"])
        self.assertEqual(code, 2)
        self.assertIn("unknown command: nope", err.getvalue())

    def test_main_requires_a_command(self):
        with self.assertRaises(SystemExit) as ctx:
            main([])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
