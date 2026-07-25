"""Business audit logging module.

Provides:
  - BizChangeLogContext: request-scoped context for operator info (user, IP, UA)
  - AuditService: persist and query audit log entries
  - AuditMiddleware: FastAPI middleware to capture request context
  - audit_api: Admin-only API for query audit history
"""
