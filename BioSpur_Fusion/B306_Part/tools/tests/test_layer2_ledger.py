import unittest

from B306_Part.tools.layer2_ledger import (
    imu_missing_record_causes,
    ledger_between,
)


def snapshot(
    *,
    enq_imu=0,
    enq_uwb=0,
    enq_ctl=0,
    q_drop_imu=0,
    q_drop_uwb=0,
    q_drop_ctl=0,
    delivered_imu=0,
    delivered_uwb=0,
    delivered_ctl=0,
    abort_imu=0,
    abort_uwb=0,
    abort_ctl=0,
    imu_epoch_defer_drop=0,
):
    return locals()


class Layer2LedgerTest(unittest.TestCase):
    def test_balanced(self):
        before = snapshot(
            enq_imu=100,
            enq_uwb=20,
            enq_ctl=5,
            delivered_imu=90,
            delivered_uwb=20,
            delivered_ctl=5,
        )
        after = snapshot(
            enq_imu=1100,
            enq_uwb=220,
            enq_ctl=105,
            q_drop_imu=200,
            delivered_imu=890,
            delivered_uwb=220,
            delivered_ctl=105,
            abort_imu=2,
        )
        ledger = ledger_between(before, after)
        self.assertTrue(ledger["balanced"])
        self.assertEqual(ledger["classes"]["imu"]["residual"], 0)
        self.assertEqual(
            ledger["classes"]["imu"]["producer_aborted"], 2
        )

    def test_unbalanced_is_flagged(self):
        before = snapshot()
        after = snapshot(
            enq_imu=100,
            q_drop_imu=10,
            delivered_imu=89,
        )
        ledger = ledger_between(before, after)
        self.assertFalse(ledger["balanced"])
        self.assertEqual(ledger["classes"]["imu"]["residual"], 1)

    def test_imu_cause_ledger(self):
        before = snapshot()
        after = snapshot(
            q_drop_imu=7,
            abort_imu=2,
            imu_epoch_defer_drop=1,
        )
        self.assertEqual(
            imu_missing_record_causes(before, after),
            {
                "queue_drop": 7,
                "producer_abort": 2,
                "dk_epoch_defer": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
