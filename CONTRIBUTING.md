# Contributing

Use fictional chat IDs and mocked Telegram clients in tests.

- For scheduling changes, test timezone boundaries, next-run selection and duplicate-run prevention.
- For session changes, cover login, expired authorization and configuration persistence.
- For dashboard changes, check task editing, status updates and local request validation.
- For service changes, cover scheduler startup, shutdown and watchdog recovery.
- Include the Python version, OS and reproduction steps in bug reports; replace phone numbers, API credentials and session data with sample values.

Install `requirements.lock`, then run `python3 -m unittest discover -v`.
Include a screenshot with fictional tasks when changing the dashboard.
