import unittest
from store_extras import normalize_coupon_code, coupon_for_update

class FakeResult:
    def __init__(self, row):
        self.row = row
    def fetchone(self):
        return self.row

class FakeConnection:
    def __init__(self, coupon):
        self.coupon = coupon
        self.statements = []
    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if "SELECT * FROM store_coupons" in sql:
            return FakeResult(self.coupon)
        return FakeResult(None)

class CouponTests(unittest.TestCase):
    def coupon(self, kind="percent", value=15, maximum=None, minimum=0):
        return {"id": 8, "code": "SAVE15", "discount_type": kind,
                "discount_value": value, "max_discount": maximum,
                "min_order_amount": minimum}

    def test_normalize_code(self):
        self.assertEqual(normalize_coupon_code(" save 15! "), "SAVE15")

    def test_percentage_discount(self):
        conn = FakeConnection(self.coupon())
        result = coupon_for_update(conn, "SAVE15", 200000)
        self.assertEqual(result["discount"], 30000)
        self.assertEqual(result["payable"], 170000)
        self.assertTrue(any("used_count=used_count+1" in sql for sql, _ in conn.statements))

    def test_fixed_discount_and_cap(self):
        result = coupon_for_update(FakeConnection(self.coupon("fixed", 50000, 20000)), "SAVE15", 100000)
        self.assertEqual(result["discount"], 20000)

    def test_never_zero_out_order(self):
        result = coupon_for_update(FakeConnection(self.coupon("fixed", 90000)), "SAVE15", 50000)
        self.assertEqual(result["payable"], 1)
        self.assertEqual(result["discount"], 49999)

    def test_minimum_order(self):
        with self.assertRaises(ValueError):
            coupon_for_update(FakeConnection(self.coupon(minimum=100000)), "SAVE15", 50000)

    def test_unknown_coupon(self):
        with self.assertRaises(ValueError):
            coupon_for_update(FakeConnection(None), "NOPE", 50000)

if __name__ == "__main__":
    unittest.main()
