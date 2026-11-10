import unittest

from bellhaven_sync.normalize import is_po_box, name_similarity, norm_name, norm_phone, norm_street, norm_zip


class NormalizeTests(unittest.TestCase):
    def test_street_abbreviations(self):
        self.assertEqual(norm_street("4850 Northwest Sylvania Avenue"), norm_street("4850 NW Sylvania Ave"))
        self.assertEqual(norm_street("1120 West Main Street"), norm_street("1120 W Main St"))
        self.assertEqual(norm_street("3313 Wilmington Pk"), norm_street("3313 Wilmington Pike"))
        self.assertEqual(norm_street("199 Barks Road West"), norm_street("199 Barks Rd W"))
        self.assertNotEqual(norm_street("240 Market St"), norm_street("118 Union Square Dr"))

    def test_po_box(self):
        self.assertTrue(is_po_box("PO Box 517"))
        self.assertTrue(is_po_box("P.O. Box 12"))
        self.assertFalse(is_po_box("12 Box Elder Ln"))

    def test_name_cosmetic_variants_collapse(self):
        self.assertEqual(norm_name("The Arbors at Bellhaven - Dayton"), norm_name("Arbors at Bellhaven Dayton"))
        self.assertEqual(norm_name("Bellhaven Healthcare Centre of Ashland"), norm_name("Bellhaven Health Care Center of Ashland"))
        self.assertEqual(norm_name("Bellhaven Rehabilitation & Nursing of Grove City"), norm_name("Bellhaven Rehab and Nursing of Grove City"))
        self.assertEqual(norm_name("Bellhaven at Sycamore Ridge"), norm_name("Bellhaven of Sycamore Ridge"))

    def test_name_real_differences_do_not_collapse(self):
        self.assertNotEqual(norm_name("Bellhaven of Carlisle"), norm_name("Bellhaven of New Carlisle"))
        self.assertNotEqual(norm_name("Riverbend Manor Care Center"), norm_name("Bellhaven of Chagrin Falls"))
        self.assertGreater(name_similarity("Bellhaven of Carlisle", "Bellhaven of New Carlisle"), 0.8)

    def test_zip_phone(self):
        self.assertEqual(norm_zip("45662-1234"), "45662")
        self.assertEqual(norm_phone("(517) 573-9493"), "5175739493")
        self.assertEqual(norm_phone("1-517-573-9493"), "5175739493")


if __name__ == "__main__":
    unittest.main()
