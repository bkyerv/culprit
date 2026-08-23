# P4 live-service captures

These screenshots were captured from the deployed `culprit-control` Cloud Run URL after HTTP Basic
Auth, using the live Firestore bootstrap and SSE adapter. They are not mock-mode captures.

- `desktop-*.png`: all seven views at a 1440 × 1000 viewport.
- `mobile-investigation.png`, `mobile-outcome.png`, and `mobile-criteria.png`: the key causal,
  evidence, and disclosure views at a 390 × 844 viewport.

Chrome DevTools reported zero console warnings/errors, a 200 EventSource request after the Basic
Auth origin cache was populated, and no document-level horizontal overflow in any view at either
target width.
