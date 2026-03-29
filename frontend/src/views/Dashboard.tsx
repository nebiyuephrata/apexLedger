import React from 'react';
import { Button, Chip, Stack, Typography } from '@mui/material';
import { DataGrid, GridColDef } from '@mui/x-data-grid';
import toast from 'react-hot-toast';
import { MetricCard } from '../components/MetricCard';
import { SectionCard } from '../components/SectionCard';
import { LagStatus } from '../components/LagStatus';
import { useAgentSessionsQuery, useApplicationsQuery, useCommandCatalogQuery, useHealthQuery, useInvokeToolMutation } from '../features/ledger/hooks';
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
  const { data: applications = [] } = useApplicationsQuery();
  const { data: health = null } = useHealthQuery();
  const { data: sessions = [] } = useAgentSessionsQuery();
  const { data: commands = [] } = useCommandCatalogQuery();
  const invokeTool = useInvokeToolMutation();

  const selected = applications.find((item) => item.application_id === selectedId) ?? applications[0] ?? null;

  const kpis = [
    { label: 'Active Applications', value: String(applications.length || 0), helper: 'Projection-backed operational view' },
    { label: 'Pending Decision', value: String(applications.filter((item) => item.state === 'PENDING_DECISION').length), helper: 'Awaiting orchestration or human action' },
    { label: 'Hard Blocks', value: String(applications.filter((item) => item.compliance_status === 'BLOCKED').length), helper: 'Compliance-led auto declines' },
    { label: 'Live Sessions', value: String(sessions.filter((item) => item.status !== 'completed').length), helper: 'Gas Town session streams in-flight' },
  ];

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
          actions={<Button variant="contained" onClick={() => toast.success('submit_application staged')}>New Application</Button>}
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
                  onClick={() => toast('record_human_review will enforce override semantics', { icon: 'A' })}
                >
                  Approve with Guard
                </Button>
                <Button variant="outlined" color="inherit" onClick={() => toast.success('Human review requested')}>
                  Request Review
                </Button>
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
                          onSuccess: () => toast.success(`${command.name} sent`),
                          onError: (error) => toast.error(error instanceof Error ? error.message : 'Command failed'),
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
    </div>
  );
};
