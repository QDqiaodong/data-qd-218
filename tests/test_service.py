import unittest
from core_recovery import process


class CoreTests(unittest.TestCase):
    def base(self):
        return {"runs":[{"id":"r1","from_m":"0","to_m":"10"}],"pieces":[{"id":"p1","run_id":"r1","length_m":"4"},{"id":"p2","run_id":"r1","length_m":"5"}],"lithology":[{"id":"l1","from_m":"0","to_m":"6","code":"sand"},{"id":"l2","from_m":"6","to_m":"10","code":"clay"}]}
    def test_recovery_ratio(self):
        row=process(self.base())["runs"][0]
        self.assertEqual((row["recovered_m"],row["recovery_ratio"],row["status"]),("9","9/10","within_run"))
    def test_over_recovery_is_status(self):
        data=self.base(); data["pieces"][1]["length_m"]="7"
        self.assertEqual(process(data)["runs"][0]["status"],"over_recovered")
    def test_lithology_maps_to_run(self):
        self.assertEqual(process(self.base())["lithology"][1]["run_id"],"r1")
    def test_overlapping_runs_fail(self):
        data=self.base(); data["runs"].append({"id":"r2","from_m":"9","to_m":"20"})
        with self.assertRaises(ValueError): process(data)
    def test_cross_run_lithology_fails(self):
        data=self.base(); data["runs"].append({"id":"r2","from_m":"10","to_m":"20"}); data["lithology"]=[{"id":"l","from_m":"9","to_m":"11","code":"x"}]
        with self.assertRaises(ValueError): process(data)
    def test_overlapping_lithology_fails(self):
        data=self.base(); data["lithology"][1]["from_m"]="5"
        with self.assertRaises(ValueError): process(data)

if __name__=="__main__": unittest.main()
