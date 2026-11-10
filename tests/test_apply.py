import tempfile
import unittest
from pathlib import Path

from bellhaven_sync.apply import apply_proposal
from bellhaven_sync.crm import CRMError
from bellhaven_sync.proposals import Proposal
from bellhaven_sync.store import Store


class FakeCRM:
    def __init__(self, fail_on=None):
        self.calls = []
        self.accounts = {"OLD": {"account_id": "OLD", "chow_current_account": ""}}
        self.fail_on = fail_on

    def create_account(self, fields):
        self.calls.append(("create_account", fields))
        if self.fail_on == "create_account":
            raise CRMError("boom")
        self.accounts["NEW1"] = {"account_id": "NEW1", **fields}
        return {"account_id": "NEW1", "message": "created"}

    def update_account(self, aid, fields):
        self.calls.append(("update_account", aid, fields))
        if self.fail_on == "update_account":
            raise CRMError("boom")
        self.accounts[aid].update(fields)
        return {"account_id": aid, "message": "updated", "fields": sorted(fields)}

    def create_contact(self, fields):
        self.calls.append(("create_contact", fields))
        return {"contact_id": "C1", "message": "created"}

    def get_account(self, aid):
        return self.accounts[aid]


def chow_proposal():
    return Proposal(kind="chow", subject="OLD", title="t", summary="s", confidence="high", evidence={}, actions=[
        {"op": "create_account", "ref": "$new", "fields": {"name": "Bellhaven of Tiffin", "parent_id": "P"}},
        {"op": "update_account", "account_id": "OLD", "fields": {"chow_current_account": "$new"}},
        {"op": "create_contact", "fields": {"account_id": "$new", "name": "Walt Osei", "title": "Administrator"}},
    ])


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.store.upsert_proposals(self.store.start_run(), [chow_proposal()])
        self.pid = self.store.list_proposals("pending")[0]["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_refuses_to_write_unless_approved(self):
        with self.assertRaises(ValueError):
            apply_proposal(self.store, FakeCRM(), self.pid)

    def test_new_id_is_threaded_through_later_actions(self):
        crm = FakeCRM()
        self.store.decide(self.pid, "approved", "me")
        res = apply_proposal(self.store, crm, self.pid)
        self.assertTrue(res["ok"])
        self.assertEqual(crm.calls[1], ("update_account", "OLD", {"chow_current_account": "NEW1"}))
        self.assertEqual(crm.calls[2][1]["account_id"], "NEW1")
        self.assertEqual(self.store.get_proposal(self.pid)["status"], "applied")

    def test_failure_stops_the_bundle_and_is_recorded(self):
        crm = FakeCRM(fail_on="update_account")
        self.store.decide(self.pid, "approved", "me")
        res = apply_proposal(self.store, crm, self.pid)
        self.assertFalse(res["ok"])
        self.assertEqual([c[0] for c in crm.calls], ["create_account", "update_account"])  # contact never attempted
        p = self.store.get_proposal(self.pid)
        self.assertEqual(p["status"], "failed")
        self.assertEqual(p["result"]["refs"], {"$new": "NEW1"})  # the reviewer can see what did land


if __name__ == "__main__":
    unittest.main()
