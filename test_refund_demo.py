import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier

from refund_demo import Principal, RefundLedger, RefundRequest, RefundResult


class RefundDemoTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = self.enterContext(TemporaryDirectory())
        self.path = Path(directory) / "ledger.sqlite"
        self.ledger = RefundLedger(self.path)
        self.ledger.seed_example_orders()
        self.agent = Principal("shop-a", "agent")
        self.reviewer = Principal("shop-a", "reviewer")
        self.request = RefundRequest("review-1", "order-a", 1200)

    def refunds(self):
        with closing(sqlite3.connect(self.path)) as conn:
            return conn.execute(
                "SELECT tenant, operation_id, order_id, amount_minor FROM refunds ORDER BY operation_id"
            ).fetchall()

    def test_agent_reads_only_its_tenant_orders(self) -> None:
        self.assertEqual(self.ledger.read_order(self.agent, "order-a"), 4000)
        with self.assertRaises(PermissionError):
            self.ledger.read_order(self.agent, "order-b")

    def test_agent_cannot_self_approve_or_write_without_approval(self) -> None:
        with self.assertRaises(PermissionError):
            self.ledger.approve(self.agent, self.request)
        with self.assertRaises(PermissionError):
            self.ledger.refund(self.agent, self.request)
        self.assertEqual(self.refunds(), [])

    def test_tenant_boundary_holds_even_with_another_tenants_approval(self) -> None:
        other_tenant_request = RefundRequest("review-2", "order-b", 750)
        self.ledger.approve(Principal("shop-b", "reviewer"), other_tenant_request)
        with self.assertRaises(PermissionError):
            self.ledger.refund(self.agent, other_tenant_request)
        self.assertEqual(self.refunds(), [])

    def test_approval_and_retries_bind_to_order_and_amount(self) -> None:
        self.ledger.approve(self.reviewer, self.request)
        with self.assertRaises(PermissionError):
            self.ledger.refund(self.agent, RefundRequest("review-1", "order-a2", 1200))
        with self.assertRaises(PermissionError):
            self.ledger.refund(self.agent, RefundRequest("review-1", "order-a", 1300))
        self.assertEqual(self.refunds(), [])

        self.assertEqual(self.ledger.refund(self.agent, self.request), RefundResult.RECORDED)
        with self.assertRaises(PermissionError):
            self.ledger.approve(self.reviewer, RefundRequest("review-1", "order-a", 1300))
        with self.assertRaises(PermissionError):
            self.ledger.refund(self.agent, RefundRequest("review-1", "order-a", 1300))
        self.assertEqual(self.refunds(), [("shop-a", "review-1", "order-a", 1200)])

    def test_retry_after_reopen_keeps_one_refund_when_response_is_lost(self) -> None:
        self.ledger.approve(self.reviewer, self.request)
        self.ledger.refund(self.agent, self.request)  # Result lost after the ledger commit.

        reopened = RefundLedger(self.path)
        self.assertEqual(reopened.refund(self.agent, self.request), RefundResult.ALREADY_RECORDED)
        self.assertEqual(self.refunds(), [("shop-a", "review-1", "order-a", 1200)])

    def test_parallel_requests_create_one_refund(self) -> None:
        self.ledger.approve(self.reviewer, self.request)
        start = Barrier(8, timeout=5)

        def submit_refund():
            start.wait()
            return self.ledger.refund(self.agent, self.request)

        with ThreadPoolExecutor(max_workers=8) as threads:
            futures = [threads.submit(submit_refund) for _ in range(8)]
            results = [future.result() for future in futures]

        self.assertEqual(results.count(RefundResult.RECORDED), 1)
        self.assertEqual(results.count(RefundResult.ALREADY_RECORDED), 7)
        self.assertEqual(len(self.refunds()), 1)

    def test_approved_refunds_cannot_exceed_order_total(self) -> None:
        self.ledger.approve(self.reviewer, self.request)
        second = RefundRequest("review-2", "order-a", 3000)
        self.ledger.approve(self.reviewer, second)
        self.ledger.refund(self.agent, self.request)
        with self.assertRaises(ValueError):
            self.ledger.refund(self.agent, second)
        self.assertEqual(len(self.refunds()), 1)


if __name__ == "__main__":
    unittest.main()
