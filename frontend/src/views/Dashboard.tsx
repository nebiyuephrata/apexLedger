import React from 'react';
import { Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, TextField, Typography } from '@mui/material';
import { DataGrid, GridColDef } from '@mui/x-data-grid';
import toast from 'react-hot-toast';
import { MetricCard } from '../components/MetricCard';
import { SectionCard } from '../components/SectionCard';
import { LagStatus } from '../components/LagStatus';
import { useAgentSessionsQuery, useApplicationsQuery, useCommandCatalogQuery, useHealthQuery, useInvokeToolMutation } from '../features/ledger/hooks';
import { LedgerApiError } from '../lib/ledgerClient';
import {
  ApplicationSummary,
} from '../types/ledger';

const currency = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
});

const columns: GridColDef<ApplicationSummary>[] = [
  { field: 'application_id', headerName: 'Application', flex: 1.3, minWidth: 180 },
  { field: 'state', headerName: 'State', flex: 1.2, minWidth: 150 },
  { field: 'risk_tier', headerName: 'Risk', minWidth: 110 },
  {
    field: 'fraud_score',
    headerName: 'Fraud',
    minWidth: 110,
    valueFormatter: ({ value }) => (value == null ? '-' : Number(value).toFixed(2)),
  },
  { field: 'compliance_status', headerName: 'Compliance', minWidth: 140 },
  { field: 'last_event_type', headerName: 'Last Event', flex: 1.2, minWidth: 170 },
];

export const Dashboard: React.FC = () => {
  const [selectedId, setSelectedId] = React.useState<string>('loan-ACME-0091');
  const [newApplicationOpen, setNewApplicationOpen] = React.useState(false);
  const [reviewOpen, setReviewOpen] = React.useState(false);
  const [newApplication, setNewApplication] = React.useState({
    application_id: '',
    applicant_id: '',
    requested_amount_usd: '100000',
    loan_purpose: 'working_capital',
  });
  const [humanReview, setHumanReview] = React.useState({
    reviewer_id: 'loan-officer-1',
    final_decision: 'APPROVE',
    approved_amount_usd: '50000',
    interest_rate_pct: '5.0',
    term_months: '24',
    override_reason: '',
  });
  const { data: applicationsPage } = useApplicationsQuery();
  const { data: health = null } = useHealthQuery();
  const { data: sessionsPage } = useAgentSessionsQuery();
  const { data: commands = [] } = useCommandCatalogQuery();
  const invokeTool = useInvokeToolMutation();

  const applications = applicationsPage?.items ?? [];
  const sessions = sessionsPage?.items ?? [];

  const selected = applications.find((item) => item.application_id === selectedId) ?? applications[0] ?? null;

  const kpis = [
    { label: 'Active Applications', value: String(applicationsPage?.total ?? applications.length ?? 0), helper: 'Projection-backed operational view' },
    { label: 'Pending Decision', value: String(applications.filter((item) => item.state === 'PENDING_DECISION').length), helper: 'Awaiting orchestration or human action' },
    { label: 'Hard Blocks', value: String(applications.filter((item) => item.compliance_status === 'BLOCKED').length), helper: 'Compliance-led auto declines' },
    { label: 'Live Sessions', value: String(sessions.filter((item) => item.status !== 'completed').length), helper: 'Gas Town session streams in-flight' },
  ];

  const handleMutationError = (error: unknown) => {
    if (error instanceof LedgerApiError) {
      toast.error(`${error.message}${error.suggestedAction ? ` · ${error.suggestedAction}` : ''}`);
      return;
    }
    toast.error(error instanceof Error ? error.message : 'Command failed');
  };

  const submitApplication = () => {
    if (!newApplication.application_id || !newApplication.applicant_id) {
      toast.error('Application ID and applicant ID are required.');
      return;
    }
    invokeTool.mutate(
      {
        toolName: 'submit_application',
        payload: {
          application_id: newApplication.application_id,
          applicant_id: newApplication.applicant_id,
          requested_amount_usd: Number(newApplication.requested_amount_usd),
          loan_purpose: newApplication.loan_purpose,
        },
      },
      {
        onSuccess: ({ meta }) => {
          toast.success(
            meta?.idempotent_replay
              ? `Application replayed · ${meta.idempotency_key}`
              : `Application submitted · ${meta?.request_id ?? 'request pending'}`,
          );
          setSelectedId(newApplication.application_id);
          setNewApplicationOpen(false);
        },
        onError: handleMutationError,
      },
    );
  };

  const submitHumanReview = () => {
    if (!selected) {
      toast.error('Select an application first.');
      return;
    }
    invokeTool.mutate(
      {
        toolName: 'record_human_review',
        payload: {
          application_id: selected.application_id,
          reviewer_id: humanReview.reviewer_id,
          final_decision: humanReview.final_decision,
          approved_amount_usd: Number(humanReview.approved_amount_usd),
          interest_rate_pct: Number(humanReview.interest_rate_pct),
          term_months: Number(humanReview.term_months),
          override_reason: humanReview.override_reason || undefined,
        },
      },
      {
        onSuccess: ({ meta }) => {
          toast.success(
            meta?.idempotent_replay
              ? `Human review replayed · ${meta.idempotency_key}`
              : `Human review recorded · ${meta?.request_id ?? 'request pending'}`,
          );
          setReviewOpen(false);
        },
        onError: handleMutationError,
      },
    );
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Typography variant="h4" className="font-display">
            Loan Officer Console
          </Typography>
          <Typography variant="body2" className="max-w-3xl text-slate-400">
            This view is centered on the `projection_application_summary` read model and the MCP command surface for final human review.
          </Typography>
        </div>
        <LagStatus health={health} />
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {kpis.map((metric) => (
          <MetricCard key={metric.label} {...metric} />
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.4fr_0.9fr]">
        <SectionCard
          title="Application Summary Projection"
          subtitle="Operational one-row-per-application dashboard powered by CQRS projections."
          actions={<Button variant="contained" onClick={() => setNewApplicationOpen(true)}>New Application</Button>}
        >
          <div className="h-[420px]">
            <DataGrid
              rows={applications}
              columns={columns}
              getRowId={(row) => row.application_id}
              disableRowSelectionOnClick
              onRowClick={(params) => setSelectedId(String(params.id))}
              sx={{
                border: 0,
                color: '#e2e8f0',
                '& .MuiDataGrid-columnHeaders': { backgroundColor: 'rgba(15, 23, 42, 0.7)' },
                '& .MuiDataGrid-row:hover': { backgroundColor: 'rgba(30, 41, 59, 0.4)' },
              }}
            />
          </div>
        </SectionCard>

        <SectionCard
          title="Selected Application"
          subtitle="Projection detail plus the human decision controls that eventually append new events."
        >
          {selected ? (
            <div className="space-y-4">
              <div className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                <Typography variant="overline" className="text-slate-400">
                  {selected.application_id}
                </Typography>
                <Typography variant="h5" className="font-display">
                  {currency.format(selected.requested_amount_usd ?? 0)}
                </Typography>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Chip label={selected.state ?? 'UNKNOWN'} color="info" />
                  <Chip label={`Risk ${selected.risk_tier ?? 'N/A'}`} variant="outlined" />
                  <Chip label={`Fraud ${selected.fraud_score ?? '-'}`} variant="outlined" />
                  <Chip label={selected.compliance_status ?? 'UNKNOWN'} variant="outlined" />
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                  <Typography variant="caption" className="text-slate-400">
                    Applicant ID
                  </Typography>
                  <Typography variant="body1">{selected.applicant_id}</Typography>
                </div>
                <div className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                  <Typography variant="caption" className="text-slate-400">
                    Last Event
                  </Typography>
                  <Typography variant="body1">{selected.last_event_type}</Typography>
                </div>
                <div className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                  <Typography variant="caption" className="text-slate-400">
                    Recommendation
                  </Typography>
                  <Typography variant="body1">{selected.decision_recommendation ?? 'Pending'}</Typography>
                </div>
                <div className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                  <Typography variant="caption" className="text-slate-400">
                    Sessions Completed
                  </Typography>
                  <Typography variant="body1">{selected.agent_sessions_completed.length}</Typography>
                </div>
              </div>

              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5}>
                <Button
                  variant="contained"
                  color="success"
                  onClick={() => setReviewOpen(true)}
                >
                  Approve with Guard
                </Button>
                <Button variant="outlined" color="inherit" onClick={() => setReviewOpen(true)}>Request Review</Button>
              </Stack>
            </div>
          ) : (
            <Typography variant="body2" className="text-slate-400">
              Loading application detail...
            </Typography>
          )}
        </SectionCard>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <SectionCard
          title="Gas Town Sessions"
          subtitle="Agent sessions are the persistent memory layer. This view helps operators see where work is active or needs reconciliation."
        >
          <div className="space-y-3">
            {sessions.map((session) => (
              <div key={session.session_id} className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <Typography variant="subtitle2">{session.session_id}</Typography>
                    <Typography variant="caption" className="text-slate-400">
                      {session.agent_type} on {session.application_id}
                    </Typography>
                  </div>
                  <Chip
                    label={session.status}
                    color={
                      session.status === 'completed'
                        ? 'success'
                        : session.status === 'needs_reconciliation'
                          ? 'warning'
                          : 'info'
                    }
                    variant="outlined"
                  />
                </div>
                <Typography variant="body2" className="mt-3 text-slate-300">
                  Last node: {session.last_node} · Context source: {session.context_source}
                </Typography>
              </div>
            ))}
          </div>
        </SectionCard>

        <SectionCard
          title="Command Surface"
          subtitle="These are the actual MCP tools exposed in the repo, with their preconditions visible to keep command behavior explicit."
        >
          <div className="space-y-3">
            {commands.map((command) => (
              <div key={command.name} className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <Typography variant="subtitle2">{command.name}</Typography>
                  <Button
                    size="small"
                    variant="text"
                    color="secondary"
                    onClick={() => {
                      invokeTool.mutate(
                        { toolName: command.name },
                        {
                          onSuccess: ({ meta }) =>
                            toast.success(
                              meta?.idempotent_replay
                                ? `${command.name} replayed · ${meta.idempotency_key}`
                                : `${command.name} sent · ${meta?.request_id ?? 'request pending'}`,
                            ),
                          onError: handleMutationError,
                        },
                      );
                    }}
                  >
                    Run
                  </Button>
                </div>
                <Typography variant="body2" className="mt-2 text-slate-300">
                  {command.description}
                </Typography>
                <Typography variant="caption" className="mt-2 block text-slate-500">
                  Precondition: {command.precondition}
                </Typography>
              </div>
            ))}
          </div>
        </SectionCard>
      </div>

      <Dialog open={newApplicationOpen} onClose={() => setNewApplicationOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>New Application</DialogTitle>
        <DialogContent className="space-y-4">
          <TextField
            margin="dense"
            label="Application ID"
            fullWidth
            value={newApplication.application_id}
            onChange={(event) => setNewApplication((current) => ({ ...current, application_id: event.target.value }))}
          />
          <TextField
            margin="dense"
            label="Applicant ID"
            fullWidth
            value={newApplication.applicant_id}
            onChange={(event) => setNewApplication((current) => ({ ...current, applicant_id: event.target.value }))}
          />
          <TextField
            margin="dense"
            label="Requested Amount"
            type="number"
            fullWidth
            value={newApplication.requested_amount_usd}
            onChange={(event) => setNewApplication((current) => ({ ...current, requested_amount_usd: event.target.value }))}
          />
          <TextField
            margin="dense"
            select
            label="Loan Purpose"
            fullWidth
            value={newApplication.loan_purpose}
            onChange={(event) => setNewApplication((current) => ({ ...current, loan_purpose: event.target.value }))}
          >
            <MenuItem value="working_capital">Working Capital</MenuItem>
            <MenuItem value="equipment">Equipment</MenuItem>
            <MenuItem value="expansion">Expansion</MenuItem>
          </TextField>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setNewApplicationOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={submitApplication} disabled={invokeTool.isPending}>Submit</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={reviewOpen} onClose={() => setReviewOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>Record Human Review</DialogTitle>
        <DialogContent className="space-y-4">
          <TextField
            margin="dense"
            label="Reviewer ID"
            fullWidth
            value={humanReview.reviewer_id}
            onChange={(event) => setHumanReview((current) => ({ ...current, reviewer_id: event.target.value }))}
          />
          <TextField
            margin="dense"
            select
            label="Final Decision"
            fullWidth
            value={humanReview.final_decision}
            onChange={(event) => setHumanReview((current) => ({ ...current, final_decision: event.target.value }))}
          >
            <MenuItem value="APPROVE">Approve</MenuItem>
            <MenuItem value="DECLINE">Decline</MenuItem>
            <MenuItem value="REFER">Refer</MenuItem>
          </TextField>
          <TextField
            margin="dense"
            label="Approved Amount"
            type="number"
            fullWidth
            value={humanReview.approved_amount_usd}
            onChange={(event) => setHumanReview((current) => ({ ...current, approved_amount_usd: event.target.value }))}
          />
          <TextField
            margin="dense"
            label="Interest Rate %"
            type="number"
            fullWidth
            value={humanReview.interest_rate_pct}
            onChange={(event) => setHumanReview((current) => ({ ...current, interest_rate_pct: event.target.value }))}
          />
          <TextField
            margin="dense"
            label="Term (Months)"
            type="number"
            fullWidth
            value={humanReview.term_months}
            onChange={(event) => setHumanReview((current) => ({ ...current, term_months: event.target.value }))}
          />
          <TextField
            margin="dense"
            label="Override Reason"
            fullWidth
            multiline
            minRows={3}
            value={humanReview.override_reason}
            onChange={(event) => setHumanReview((current) => ({ ...current, override_reason: event.target.value }))}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setReviewOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={submitHumanReview} disabled={invokeTool.isPending}>Record Review</Button>
        </DialogActions>
      </Dialog>
    </div>
  );
};
