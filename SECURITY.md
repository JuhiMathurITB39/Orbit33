# Security notes

Implemented: strict input validation (types, bounds, ID whitelist, no unknown fields), parameterised SQL only, read-only DB connections, 64 KB body limit, optional constant-time API-key auth, uniform error responses without stack traces, CORS allow-list from env, no secrets in code (`.env.example`), checksum-verified model loading, the assistant never executes generated SQL, structured JSON logs without payload bodies, container runs as non-root (authored Dockerfile).
Tested: injection strings, oversize bodies, wrong/missing keys, malformed JSON, hostile assistant prompts (`tests/test_api.py`).
Not implemented: user accounts/RBAC, rate limiting, TLS termination (assumes ALB/WAF), audit logging, secret rotation, dependency scanning, a penetration test. The Terraform keeps the service internal and stores the key in Secrets Manager but was never applied.
Data: all data is simulated or public-schema; no personal data is handled.
