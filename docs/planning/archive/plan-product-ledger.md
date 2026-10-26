# Plan: product work ledger

Archived: **built as ADR-0040** (`ledger.py`, `tests/test_ledger.py`). The ADR is the reference.

From Saravana's test of the repo assessment (2026-10).

**Problem**
- Issues, risks, test gaps and next changes lived inside each assessment's change.
- A new assessment started from scratch, and only "Next changes" offered "Make this change".
- Nothing recorded which change fixed what, what was still open, or what was deliberately left.

**Proposal (as built)**
- One ledger per product, every item with a stable fingerprint and its origin iteration.
- Status: open → in change #N → fixed (verified by a later run) | still present | won't do (reason) | accepted risk (reason, expiry).
- Make a change from any item or several; the change records the item ids it addresses; its PR lists them.
- Each change shows Asked / Done / Verified / Gathered.
- A pure core (`ledger.py`): fingerprint, status transitions, reconcile.
