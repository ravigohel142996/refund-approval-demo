"""Offline example of a refund tool with a synthetic SQLite ledger."""

import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


@dataclass(frozen=True)
class Principal:
    tenant: str
    role: str


@dataclass(frozen=True)
class RefundRequest:
    operation_id: str
    order_id: str
    amount_minor: int


class RefundResult(str, Enum):
    RECORDED = "recorded"
    ALREADY_RECORDED = "already_recorded"


class RefundLedger:
    """Accept principals only from the application's authenticated boundary."""

    def __init__(self, path: Path) -> None:
        self.path = path
        with closing(sqlite3.connect(self.path)) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS orders (
                    tenant TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    total_minor INTEGER NOT NULL CHECK (total_minor > 0),
                    PRIMARY KEY (tenant, order_id)
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    tenant TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
                    PRIMARY KEY (tenant, operation_id)
                );
                CREATE TABLE IF NOT EXISTS refunds (
                    tenant TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
                    PRIMARY KEY (tenant, operation_id)
                );
            """)

    @contextmanager
    def _write_transaction(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            yield conn

    def seed_example_orders(self) -> None:
        with self._write_transaction() as conn:
            conn.executemany("INSERT OR IGNORE INTO orders VALUES (?, ?, ?)", [
                ("shop-a", "order-a", 4000),
                ("shop-a", "order-a2", 4000),
                ("shop-b", "order-b", 2500),
            ])

    @staticmethod
    def _order_total(conn: sqlite3.Connection, tenant: str, order_id: str) -> int:
        row = conn.execute(
            "SELECT total_minor FROM orders WHERE tenant = ? AND order_id = ?",
            (tenant, order_id),
        ).fetchone()
        if row is None:
            raise PermissionError("order is outside this tenant")
        return row[0]

    @staticmethod
    def _validate_request(request: RefundRequest, total: int) -> None:
        if not request.operation_id or type(request.amount_minor) is not int:
            raise ValueError("refund requires an operation ID and an integer amount")
        if not 0 < request.amount_minor <= total:
            raise ValueError("amount is outside the order total")

    def read_order(self, actor: Principal, order_id: str) -> int:
        with closing(sqlite3.connect(self.path)) as conn:
            return self._order_total(conn, actor.tenant, order_id)

    def approve(self, reviewer: Principal, request: RefundRequest) -> None:
        if reviewer.role != "reviewer":
            raise PermissionError("a reviewer must approve the refund")
        with self._write_transaction() as conn:
            total = self._order_total(conn, reviewer.tenant, request.order_id)
            self._validate_request(request, total)
            terms = (request.order_id, request.amount_minor)
            key = (reviewer.tenant, request.operation_id)
            conn.execute("INSERT OR IGNORE INTO approvals VALUES (?, ?, ?, ?)", (*key, *terms))
            approved = conn.execute(
                "SELECT order_id, amount_minor FROM approvals WHERE tenant = ? AND operation_id = ?",
                key,
            ).fetchone()
            if approved != terms:
                raise PermissionError("operation is already bound to different terms")

    def refund(self, actor: Principal, request: RefundRequest) -> RefundResult:
        if actor.role != "agent":
            raise PermissionError("the refund tool requires an agent principal")
        with self._write_transaction() as conn:
            total = self._order_total(conn, actor.tenant, request.order_id)
            self._validate_request(request, total)
            terms = (request.order_id, request.amount_minor)
            key = (actor.tenant, request.operation_id)
            approved = conn.execute(
                "SELECT order_id, amount_minor FROM approvals WHERE tenant = ? AND operation_id = ?",
                key,
            ).fetchone()
            if approved != terms:
                raise PermissionError("refund does not match an approval")

            recorded = conn.execute(
                "SELECT order_id, amount_minor FROM refunds WHERE tenant = ? AND operation_id = ?",
                key,
            ).fetchone()
            if recorded is not None:
                if recorded != terms:
                    raise PermissionError("operation is already bound to different terms")
                return RefundResult.ALREADY_RECORDED

            refunded_total = conn.execute(
                "SELECT COALESCE(SUM(amount_minor), 0) FROM refunds WHERE tenant = ? AND order_id = ?",
                (actor.tenant, request.order_id),
            ).fetchone()[0]
            if refunded_total + request.amount_minor > total:
                raise ValueError("refund would exceed the order total")
            conn.execute("INSERT INTO refunds VALUES (?, ?, ?, ?)", (*key, *terms))
            return RefundResult.RECORDED
