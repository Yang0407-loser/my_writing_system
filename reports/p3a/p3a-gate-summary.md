# P3A Projection Gate Summary

- Evidence: `reports/p3a/p3a-gate-evidence.json`
- Schema: `p3a-projection-gate-v1`
- Backend: PostgreSQL 17 (`writer_test`)
- Scenarios: 16 passed, 0 failed, 0 skipped
- Final lag: 0 events on the dedicated final-lag scope
- Projectors: 7 delete/rebuild/reconcile manifests matched
- Evidence SHA-256: `72B2D559F21C071BB0D0FBDB1F8A2B004B36B8A36427A79970D59F81E1C7DA0E`
- Verifier: `{"passed": true, "errors": []}`

The Gate proves PostgreSQL scheduling truth, fenced leases, ordered Delivery,
dead-letter/requeue audit, crash-resume, Canon commits during rebuild,
bootstrap activation-gap handling, duplicate wake-up convergence, and scanner
recovery without Celery messages. It does not grant external production
readiness; P3B remains required.
