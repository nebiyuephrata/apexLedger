import React from 'react';
import { Button, Chip, TextField, Typography } from '@mui/material';
import toast from 'react-hot-toast';
import { DataGrid, GridColDef } from '@mui/x-data-grid';
import { SectionCard } from '../components/SectionCard';
import { useAgentPerformanceQuery } from '../features/ledger/hooks';
import { AgentPerformance } from '../types/ledger';

const columns: GridColDef<AgentPerformance>[] = [
  { field: 'agent_type', headerName: 'Agent', flex: 1.1, minWidth: 160 },
  { field: 'model_version', headerName: 'Model', minWidth: 120 },
  { field: 'analyses_completed', headerName: 'Analyses', minWidth: 120 },
  {
    field: 'avg_confidence',
    headerName: 'Avg Confidence',
    minWidth: 140,
    valueFormatter: ({ value }) => Number(value).toFixed(2),
  },
  {
    field: 'avg_duration_ms',
    headerName: 'Avg Duration',
    minWidth: 150,
    valueFormatter: ({ value }) => `${Math.round(Number(value) / 1000)}s`,
  },
  {
    field: 'override_rate',
    headerName: 'Override Rate',
    minWidth: 140,
    valueFormatter: ({ value }) => `${(Number(value) * 100).toFixed(1)}%`,
  },
];

export const Admin: React.FC = () => {
  const { data: rows = [] } = useAgentPerformanceQuery();

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Typography variant="h4" className="font-display">
            Agent Performance Ledger
          </Typography>
          <Typography variant="body2" className="max-w-3xl text-slate-400">
            Admins monitor model behavior here, using the `projection_agent_performance` read model and the what-if machinery that lives beside it.
          </Typography>
        </div>
        <div className="flex gap-3">
          <Button variant="outlined" color="inherit" onClick={() => toast.success('Projection rebuild requested')}>
            Rebuild Projection
          </Button>
          <Button variant="contained" onClick={() => toast.success('Model comparison queued')}>
            Compare Models
          </Button>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
        <SectionCard
          title="Model Comparison"
          subtitle="Aggregated operational performance by agent type and model version."
        >
          <div className="h-[360px]">
            <DataGrid
              rows={rows}
              columns={columns}
              getRowId={(row) => `${row.agent_type}-${row.model_version}`}
              disableRowSelectionOnClick
              sx={{
                border: 0,
                color: '#e2e8f0',
                '& .MuiDataGrid-columnHeaders': { backgroundColor: 'rgba(15, 23, 42, 0.7)' },
              }}
            />
          </div>
        </SectionCard>

        <div className="space-y-4">
          <SectionCard
            title="What-If Projector"
            subtitle="Counterfactual replay without touching the real event store."
          >
            <div className="space-y-3">
              <TextField label="Application ID" defaultValue="loan-ACME-0091" fullWidth />
              <TextField label="Branch Point" defaultValue="CreditAnalysisCompleted@global_position:14397" fullWidth />
              <TextField label="Counterfactual" defaultValue="risk_tier=HIGH" fullWidth />
              <Button variant="contained" onClick={() => toast.success('Counterfactual simulation started')}>
                Run What-If
              </Button>
            </div>
          </SectionCard>

          <SectionCard
            title="Projection Operations"
            subtitle="Blue-green rebuild and lag discipline."
          >
            <div className="space-y-3">
              <div className="flex items-center justify-between rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                <Typography variant="body2">Application Summary SLO</Typography>
                <Chip label="<500ms" color="success" variant="outlined" />
              </div>
              <div className="flex items-center justify-between rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                <Typography variant="body2">Compliance Audit SLO</Typography>
                <Chip label="<2s" color="warning" variant="outlined" />
              </div>
              <div className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                <Typography variant="subtitle2">Rebuild Contract</Typography>
                <Typography variant="body2" className="mt-2 text-slate-300">
                  Rebuilds must replay from global position zero without mutating historical events.
                </Typography>
              </div>
            </div>
          </SectionCard>
        </div>
      </div>
    </div>
  );
};
