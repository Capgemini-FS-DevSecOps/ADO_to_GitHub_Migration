# SSO Stretch Goals (FR-022a)

Extension points for enterprise SSO — **not implemented in v1**.

- Accelerator API: add `Authorization` middleware validating OIDC JWT from IdP.
- UI: Next.js middleware redirect to SSO login; store session in httpOnly cookie.
- Agent service: propagate `actor` and roles from JWT claims into RBAC (`ProfileRole`).
- Audit: record `actor` from SSO subject id, not free-text fields.

Configure via env: `ADO2GH_SSO_ISSUER`, `ADO2GH_SSO_AUDIENCE`, `ADO2GH_SSO_JWKS_URL`.
