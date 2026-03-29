# Ledger Frontend

This frontend is a `Vite + React + Tailwind + MUI` console aligned to the existing Ledger backend:

- Commands map to MCP tools.
- Read views map to projections and resources.
- Actor views are role-gated for `loan_officer`, `compliance_officer`, `security_officer`, `admin`, `auditor`, `applicant`, and `user_proxy`.

## Runtime Modes

The UI supports two data modes:

- `mock mode`
  Uses the built-in frontend adapter in `src/lib/ledgerMock.ts`.
- `api mode`
  Uses HTTP endpoints configured through `VITE_LEDGER_API_BASE_URL`.

## Environment

Use `frontend/.env.example` as the template.

```env
VITE_CLERK_PUBLISHABLE_KEY=
VITE_LEDGER_API_BASE_URL=http://localhost:8000
VITE_LEDGER_USE_MOCK=true
```

Behavior:

- If `VITE_LEDGER_USE_MOCK=true`, the UI always uses mock data.
- If `VITE_LEDGER_USE_MOCK=false`, the UI uses the HTTP API client.
- In local development, if no API base URL is configured, the app falls back to mock mode.

## Expected API Contract

The current frontend expects these HTTP endpoints:

- `GET /api/applications`
- `GET /api/applications/:id`
- `GET /api/applications/:id/compliance?as_of=...`
- `GET /api/applications/:id/audit-trail`
- `GET /api/agents/performance?agent_type=...`
- `GET /api/agents/sessions`
- `GET /api/ledger/health`
- `GET /api/meta/commands`
- `GET /api/meta/actors`
- `GET /api/ops/logs`
- `POST /api/tools/:toolName`

These mirror the current Ledger concepts:

- `ApplicationSummary`
- `ComplianceAuditView`
- `AgentPerformanceLedger`
- `AgentSession` recovery status
- MCP tool invocation

## Development

```bash
npm install
npm run dev
```

## Production Build

```bash
npm run build
```
