import tempfile
import unittest
from pathlib import Path

from bellhaven_sync.proposals import Proposal
from bellhaven_sync.store import Store


def prop(subject, fields):
    return Proposal(kind="update", subject=subject, title=subject, summary="s", confidence="high", evidence={},
                    actions=[{"op": "update_account", "account_id": subject, "fields": fields}])


class StoreTests(unittest.TestCase):
    def test_rerun_does_not_requeue_decided_proposals(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(Path(d) / "t.db")
            r1 = s.start_run()
            c = s.upsert_proposals(r1, [prop("A", {"name": "x"}), prop("B", {"name": "y"})])
            self.assertEqual(c["new"], 2)
            a, b = s.list_proposals("pending")
            s.decide(a["id"], "rejected", "me")
            s.decide(b["id"], "approved", "me")
            s.mark_applied(b["id"], {"ok": True}, True)
            # second run regenerates the same two + one new
            r2 = s.start_run()
            c = s.upsert_proposals(r2, [prop("A", {"name": "x"}), prop("B", {"name": "y"}), prop("C", {"name": "z"})])
            self.assertEqual((c["new"], c["already_decided"]), (1, 2))
            self.assertEqual([p["subject"] for p in s.list_proposals("pending")], ["C"])
            self.assertEqual(c["reappeared_after_apply"], [b["id"]])  # surfaced as a warning
            # third run: C vanished -> withdrawn
            r3 = s.start_run()
            c = s.upsert_proposals(r3, [])
            self.assertEqual(c["withdrawn"], 1)
            self.assertEqual(s.list_proposals("pending"), [])


if __name__ == "__main__":
    unittest.main()
