"""Show the refund approval and retry behavior with synthetic records."""

from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from refund_demo import Principal, RefundLedger, RefundRequest, RefundResult


def refund_count(path: Path) -> int:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT COUNT(*) FROM refunds").fetchone()[0]


def main() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "refunds.sqlite"
        ledger = RefundLedger(path)
        ledger.seed_example_orders()
        agent = Principal("shop-a", "agent")
        reviewer = Principal("shop-a", "reviewer")
        request = RefundRequest("refund-001", "order-a", 1200)

        total = ledger.read_order(agent, request.order_id)
        print(f"Authorized order lookup: {request.order_id}, total {total} minor units")

        try:
            ledger.refund(agent, request)
        except PermissionError:
            if refund_count(path) != 0:
                raise RuntimeError("An unapproved request changed the ledger")
            print("Unapproved refund: blocked | 0 ledger entries")
        else:
            raise RuntimeError("The approval check did not block the refund")

        ledger.approve(reviewer, request)
        ledger.refund(agent, request)  # Discard the acknowledgement to simulate a lost reply.
        print("Approved refund: acknowledgement discarded after the commit")

        reopened = RefundLedger(path)
        retry = reopened.refund(agent, request)
        if retry is not RefundResult.ALREADY_RECORDED or refund_count(path) != 1:
            raise RuntimeError("Retry did not resolve to the single existing refund")
        print("Retry after reopening: already recorded | 1 ledger entry")


if __name__ == "__main__":
    main()
