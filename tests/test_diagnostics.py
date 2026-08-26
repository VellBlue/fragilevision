from __future__ import annotations

import unittest

from fragilevision.diagnostics import calculate_failure_diagnostics


class DiagnosticTests(unittest.TestCase):
    def test_dark_subgroup_is_reported_when_risk_is_higher(self):
        rows=[]
        for image,answer in [(1,"no"),(2,"no"),(3,"yes"),(4,"yes")]:
            rows.append({"image_id":image,"variant_id":1,"variant_name":"Canonica","mutation_type":"canonical",
                "ground_truth":"yes","answer":answer,"format_valid":True,"source_group":""})
        features={1:{"brightness":.1,"contrast":.2,"edge_density":.03,"width":800,"height":800},
                  2:{"brightness":.2,"contrast":.2,"edge_density":.03,"width":800,"height":800},
                  3:{"brightness":.6,"contrast":.2,"edge_density":.03,"width":800,"height":800},
                  4:{"brightness":.7,"contrast":.2,"edge_density":.03,"width":800,"height":800}}
        result=calculate_failure_diagnostics(rows,features)
        dark=next(item for item in result["risk_patterns"] if item["key"]=="dark")
        self.assertEqual(dark["failure_rate"],1.0)
        self.assertEqual(dark["delta"],1.0)


if __name__ == "__main__": unittest.main()
