import unittest
from main import _eval_rule, find_matching_filter, DEFAULT_CONFIG

class TestRuleEvaluation(unittest.TestCase):
    def test_eq_match(self):
        rule = {"field": "user", "op": "eq", "value": 1}
        self.assertTrue(_eval_rule(rule, {"user": 1}))

    def test_eq_no_match(self):
        rule = {"field": "user", "op": "eq", "value": 2}
        self.assertFalse(_eval_rule(rule, {"user": 1}))

    def test_neq(self):
        rule = {"field": "user", "op": "neq", "value": 2}
        self.assertTrue(_eval_rule(rule, {"user": 1}))

    def test_in_match(self):
        rule = {"field": "tags", "op": "in", "value": "admin"}
        self.assertTrue(_eval_rule(rule, {"tags": ["admin", "user"]}))

    def test_nin_match(self):
        rule = {"field": "tags", "op": "nin", "value": "guest"}
        self.assertTrue(_eval_rule(rule, {"tags": ["admin"]}))

    def test_nested_field(self):
        rule = {"field": "user.id", "op": "eq", "value": 42}
        self.assertTrue(_eval_rule(rule, {"user": {"id": 42}}))

    def test_recursive_field_search(self):
        # Test finding field anywhere in nested structure
        rule = {"field": "id", "op": "eq", "value": 123}
        data = {
            "user": {
                "profile": {
                    "id": 123
                }
            },
            "metadata": {
                "other_id": 456
            }
        }
        self.assertTrue(_eval_rule(rule, data))

    def test_recursive_field_search_deep(self):
        # Test finding field in deeply nested structure
        rule = {"field": "target_value", "op": "eq", "value": "found"}
        data = {
            "level1": {
                "level2": {
                    "level3": {
                        "items": [
                            {"name": "item1"},
                            {"target_value": "found"}
                        ]
                    }
                }
            }
        }
        self.assertTrue(_eval_rule(rule, data))

    def test_find_matching_filter(self):
        # Use DEFAULT_CONFIG which contains a rule for user==1
        matched = find_matching_filter({"user": 1}, DEFAULT_CONFIG)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.get("field"), "user")

if __name__ == '__main__':
    unittest.main()
