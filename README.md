# Refund approval and safe retries

A Python and SQLite demo of the write boundary behind a support agent. Orders,
approvals, and refunds are synthetic. Every amount is in minor currency units.

Run with Python 3.11 or newer.

```sh
python3 -m unittest -v
```

The tests show that an agent can read its tenant's order, but cannot approve
its own refund or use a different tenant's approval. A reviewer approves an
operation for one order and amount. A retry after reopening the service
acknowledges the existing refund, and eight parallel requests with the same approval leave
one refund row. The tests read SQLite directly to count writes.

SQLite serializes approval checks and ledger writes in one transaction. A
unique tenant and operation ID identifies each refund. The demo assumes the
caller obtains the principal and role from an authenticated application
boundary. For the AgntID challenge, a real integration must supply and enforce
those decisions before invoking the tool. This demo uses local role checks and
a simulated refund ledger with no payment provider or network calls.
