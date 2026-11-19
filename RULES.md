Read docs/problem.md first. It is the full spec.
Stack: Python 3.12, Django 5.2 LTS, PostgreSQL 16, HTMX and Tailwind via CDN, Docker Compose.
Money: integer paise only. Quantities: Decimal. Never use floats.
Stock is never a stored number. Balance = SUM(StockMovement.qty).
StockMovement rows are never edited or deleted.
All commands run through Docker.
Do only the task in the current prompt. Run the tests and fix every error before finishing.