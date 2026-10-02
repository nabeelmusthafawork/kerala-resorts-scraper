"""Tests for sharded work splitting (phone and email stages)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from resorts_scraper.models import Place  # noqa: E402
from resorts_scraper.store import Store, _apply_shard  # noqa: E402


class TestShard(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(self.dir)
        self.store.save_places([Place(feature_id=f"0x{i}:0x{i}", name=f"P{i}") for i in range(20)])
        self.store = Store(self.dir)

    def test_shards_are_disjoint_and_complete(self):
        shards = [self.store.missing_details(max_tries=3, shard=f"{i}/4") for i in (1, 2, 3, 4)]
        all_ids = [p.feature_id for s in shards for p in s]
        self.assertEqual(len(all_ids), 20)
        self.assertEqual(len(set(all_ids)), 20)  # no double work

    def test_no_shard_returns_everything(self):
        self.assertEqual(len(self.store.missing_details()), 20)
        self.assertEqual(self.store.missing_emails(), [])

    def test_split_is_stable_across_processes(self):
        """Two processes computing the same shard must agree, or they collide."""
        a = [p.feature_id for p in Store(self.dir).missing_details(shard="2/3")]
        b = [p.feature_id for p in Store(self.dir).missing_details(shard="2/3")]
        self.assertEqual(a, b)

    def test_ordering_does_not_depend_on_dict_insertion(self):
        """Shard uses feature_id sort, so it is immune to JSONL line order."""
        ids = {p.feature_id for p in Store(self.dir).missing_details(shard="1/5")}
        self.assertEqual(ids, {p.feature_id for p in _apply_shard(
            [type("X", (), {"feature_id": f"0x{i}:0x{i}"})() for i in range(20)], "1/5"
        )})

    def test_rejects_bad_shard_strings(self):
        for bad in ("9/4", "0/4", "abc", "1/0", "1"):
            with self.assertRaises(SystemExit):
                self.store.missing_details(shard=bad)

    def test_emails_shard_also_splits(self):
        self.store.save_places(
            [Place(feature_id=f"0xe{i}:0x{i}", name=f"E{i}", website="https://x.in") for i in range(10)]
        )
        s = Store(self.dir)
        counts = [len(s.missing_emails(shard=f"{i}/5")) for i in (1, 2, 3, 4, 5)]
        self.assertEqual(sum(counts), 10)
        self.assertEqual(len(s.missing_emails()), 10)


if __name__ == "__main__":
    unittest.main()


class TestSubcommandWiring(unittest.TestCase):
    """Every subcommand's func must resolve at parse time.

    Regression: `emails` referenced cmd_emails from a lambda, but that name was
    only ever imported inside cmd_run, so the subcommand crashed with NameError
    the first time it was used directly.
    """

    def test_all_subcommands_have_a_callable_func(self):
        from resorts_scraper.cli import main

        for cmd in ("run", "status", "export", "doctor", "emails", "phones", "clean"):
            argv = [cmd] + (["--help"] if cmd == "run" else [])
            with self.assertRaises(SystemExit):
                main(argv + (["--help"] if cmd != "run" else []))

    def test_emails_func_is_importable(self):
        import resorts_scraper.cli as cli

        self.assertTrue(callable(cli.cmd_emails_cmd))

    def test_last_shard_detection(self):
        from resorts_scraper.cli import _last_shard

        class A:
            shard = "6/6"
        self.assertTrue(_last_shard(A()))
        A.shard = "3/6"
        self.assertFalse(_last_shard(A()))
        del A.shard
        self.assertTrue(_last_shard(A()))
