import unittest

from bellhaven_sync.matcher import choose_survivor, find_successor, match_locations
from bellhaven_sync.proposals import proposals_for_divested, proposals_for_match
from bellhaven_sync.scraper import Location

PARENT = "P_BELL"
OTHER = "P_OTHER"


def acct(aid, name, street, city, state, zip_, parent=PARENT, rev=0, ar=0, status="Active", phone="", **kw):
    return {"account_id": aid, "name": name, "parent_id": parent, "parent_name": {PARENT: "Bellhaven Senior Living (Parent Account)", OTHER: "Other Co (Parent Account)", "": ""}[parent],
            "billing_street": street, "billing_city": city, "billing_state": state, "billing_zip": zip_,
            "care_type": "Assisted Living", "status": status, "phone": phone, "lifetime_revenue": rev,
            "outstanding_ar": ar, "chow_current_account": "", "duplicate_of_account": "", "note": "", **kw}


def loc(slug, name, street, city, state, zip_, phone="", admin="", care=("Assisted Living",)):
    return Location(slug=slug, name=name, street=street, city=city, state=state, zip=zip_, care_offerings=list(care),
                    administrator=admin, phone=phone, url=f"https://x/communities/{slug}")


class MatcherTests(unittest.TestCase):
    def run_match(self, locs, accts, contacts=()):
        return match_locations(locs, accts, list(contacts), PARENT)

    def test_address_match_beats_cosmetic_name_difference(self):
        a = acct("A1", "Arbors at Bellhaven Dayton", "2210 Salem Ave", "Dayton", "OH", "45406")
        [mr] = self.run_match([loc("d", "The Arbors at Bellhaven - Dayton", "2210 Salem Ave", "Dayton", "OH", "45406")], [a])
        self.assertEqual(mr.survivor.account_id, "A1")
        self.assertEqual(mr.survivor.tier, "A")
        ps, row = proposals_for_match(mr, PARENT, "Bellhaven", "tok")
        self.assertEqual(ps, [])  # no change: cosmetic rename suppressed
        self.assertIn("cosmetic", row["note"])

    def test_same_name_different_state_is_not_a_match(self):
        a = acct("A1", "Amberly Manor", "918 S Nevada Ave", "Colorado Springs", "CO", "80903", parent=OTHER)
        [mr] = self.run_match([loc("am", "Amberly Manor", "4390 Darrow Rd", "Hudson", "OH", "44236")], [a])
        self.assertIsNone(mr.survivor)
        self.assertEqual([c.account_id for c in mr.near], ["A1"])  # shown as evidence only
        ps, _ = proposals_for_match(mr, PARENT, "Bellhaven", "tok")
        self.assertEqual(ps[0].kind, "create")
        self.assertEqual(ps[0].confidence, "medium")

    def test_union_square_is_near_miss_not_match(self):
        a = acct("A1", "Union Square Senior Living", "240 Market St", "New Albany", "OH", "43054", parent=OTHER)
        [mr] = self.run_match([loc("us", "Bellhaven at Union Square", "118 Union Square Dr", "New Albany", "OH", "43054")], [a])
        self.assertIsNone(mr.survivor)
        self.assertEqual([c.account_id for c in mr.near], ["A1"])

    def test_carlisle_vs_new_carlisle(self):
        a = acct("A1", "Bellhaven of New Carlisle", "875 Elm St", "New Carlisle", "OH", "45344")
        [mr] = self.run_match([loc("c", "Bellhaven of Carlisle", "640 Walnut Bottom Rd", "Carlisle", "PA", "17015")], [a])
        self.assertIsNone(mr.survivor)

    def test_po_box_matches_on_name_city_phone(self):
        a = acct("A1", "Bellhaven of Ashtabula", "PO Box 517", "Ashtabula", "OH", "44004", phone="(260) 440-4975")
        [mr] = self.run_match([loc("a", "Bellhaven of Ashtabula", "3156 W Prospect Rd", "Ashtabula", "OH", "44004", phone="(260) 440-4975")], [a])
        self.assertEqual(mr.survivor.tier, "B")
        ps, _ = proposals_for_match(mr, PARENT, "Bellhaven", "tok")
        self.assertEqual(ps[0].confidence, "low")
        self.assertEqual(ps[0].actions[0]["fields"]["billing_street"], "3156 W Prospect Rd")

    def test_zip_typo_fix(self):
        a = acct("A1", "Bellhaven of Portsmouth", "2222 Gallia St", "Portsmouth", "OH", "45626")
        [mr] = self.run_match([loc("p", "Bellhaven of Portsmouth", "2222 Gallia St", "Portsmouth", "OH", "45662")], [a])
        ps, _ = proposals_for_match(mr, PARENT, "Bellhaven", "tok")
        self.assertEqual(ps[0].actions[0]["fields"]["billing_zip"], "45662")

    def test_reparent_direct_when_no_ar(self):
        a = acct("A1", "Bellhaven Crossings of Lima", "3115 N Cole St", "Lima", "OH", "45801", parent=OTHER, rev=47000, ar=0)
        [mr] = self.run_match([loc("l", "Bellhaven Crossings of Lima", "3115 N Cole St", "Lima", "OH", "45801")], [a])
        ps, _ = proposals_for_match(mr, PARENT, "Bellhaven", "tok")
        self.assertEqual(ps[0].kind, "reparent")
        self.assertEqual(ps[0].actions[0]["fields"]["parent_id"], PARENT)

    def test_chow_when_revenue_and_ar(self):
        a = acct("A1", "Bellhaven of Tiffin", "45 St Lawrence Dr", "Tiffin", "OH", "44883", parent=OTHER, rev=84000, ar=12400)
        [mr] = self.run_match([loc("t", "Bellhaven of Tiffin", "45 St Lawrence Dr", "Tiffin", "OH", "44883", admin="Walt Osei")], [a])
        ps, _ = proposals_for_match(mr, PARENT, "Bellhaven", "tok")
        self.assertEqual(ps[0].kind, "chow")
        ops = [x["op"] for x in ps[0].actions]
        self.assertEqual(ops, ["create_account", "update_account", "create_contact"])
        self.assertEqual(ps[0].actions[0]["fields"]["parent_id"], PARENT)
        # old account only gets the chow pointer, nothing else
        self.assertEqual(set(ps[0].actions[1]["fields"]), {"chow_current_account"})
        self.assertEqual(ps[0].actions[1]["fields"]["chow_current_account"], "$new")

    def test_duplicates_elect_bellhaven_record(self):
        keep = acct("A2", "Bellhaven of Port Clinton", "1420 Harbor Point Dr", "Port Clinton", "OH", "43452")
        old = acct("A1", "Harborview Nursing & Rehab of Port Clinton", "1420 Harbor Point Drive", "Port Clinton", "OH", "43452", parent=OTHER)
        [mr] = self.run_match([loc("pc", "Bellhaven of Port Clinton", "1420 Harbor Point Dr", "Port Clinton", "OH", "43452")], [old, keep])
        self.assertEqual(mr.survivor.account_id, "A2")
        ps, _ = proposals_for_match(mr, PARENT, "Bellhaven", "tok")
        self.assertEqual([p.kind for p in ps], ["duplicate"])
        f = ps[0].actions[0]["fields"]
        self.assertEqual((f["status"], f["duplicate_of_account"]), ("Inactive", "A2"))

    def test_survivor_prefers_billing_history(self):
        a = acct("A1", "X", "1 Main St", "Town", "OH", "11111", parent=OTHER, rev=5000)
        b = acct("A2", "X", "1 Main St", "Town", "OH", "11111", parent=PARENT)
        [mr] = self.run_match([loc("x", "X", "1 Main St", "Town", "OH", "11111")], [a, b])
        self.assertEqual(mr.survivor.account_id, "A1")

    def test_inactive_and_already_retired_accounts_are_ignored(self):
        a = acct("A1", "Bellhaven of Saline", "980 W Michigan Ave", "Saline", "MI", "48176", status="Inactive")
        b = acct("A2", "Bellhaven of Saline", "980 W Michigan Ave", "Saline", "MI", "48176", duplicate_of_account="A3")
        [mr] = self.run_match([loc("s", "Bellhaven of Saline", "980 W Michigan Ave", "Saline", "MI", "48176")], [a, b])
        self.assertIsNone(mr.survivor)
        self.assertEqual(len(mr.near), 2)

    def test_divested_with_successor_and_ar_gets_chow_link(self):
        old = acct("A1", "Bellhaven of Sandusky", "2715 Columbus Ave", "Sandusky", "OH", "44870", rev=130000, ar=5200)
        new = acct("A2", "Millstone Care of Sandusky", "2715 Columbus Ave", "Sandusky", "OH", "44870", parent=OTHER)
        self.assertEqual(find_successor(old, [old, new])["account_id"], "A2")
        ps = proposals_for_divested([old, new], set(), PARENT, "Bellhaven", "tok", "https://x")
        self.assertEqual(ps[0].kind, "divest_chow")
        self.assertEqual(ps[0].actions[0]["fields"]["chow_current_account"], "A2")

    def test_divested_without_successor_needs_review(self):
        old = acct("A1", "Bellhaven of Coldwater", "90 N Michigan Ave", "Coldwater", "MI", "49036")
        ps = proposals_for_divested([old], set(), PARENT, "Bellhaven", "tok", "https://x")
        self.assertEqual(ps[0].kind, "divest_review")
        self.assertEqual(ps[0].actions[0]["fields"]["status"], "Needs Review")

    def test_fingerprint_is_stable_across_note_dates(self):
        a = acct("A1", "Riverbend Manor Care Center", "150 River St", "Chagrin Falls", "OH", "44022")
        l = loc("cf", "Bellhaven of Chagrin Falls", "150 River St", "Chagrin Falls", "OH", "44022")
        [m1] = self.run_match([l], [a]); [m2] = self.run_match([l], [a])
        p1, _ = proposals_for_match(m1, PARENT, "B", "t"); p2, _ = proposals_for_match(m2, PARENT, "B", "t")
        self.assertEqual(p1[0].fingerprint, p2[0].fingerprint)


if __name__ == "__main__":
    unittest.main()
