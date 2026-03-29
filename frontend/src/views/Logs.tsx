import React, { startTransition, useDeferredValue } from 'react';
import { Chip, TextField, Typography } from '@mui/material';
import { SectionCard } from '../components/SectionCard';
import { useLogsQuery } from '../features/ledger/hooks';
import { ActivityLog } from '../types/ledger';

const levelTone: Record<ActivityLog['level'], 'success' | 'warning' | 'error'> = {
  INFO: 'success',
  WARN: 'warning',
  ERROR: 'error',
};

export const Logs: React.FC = () => {
  const [query, setQuery] = React.useState('');
  const [componentFilter, setComponentFilter] = React.useState('');
  const deferredQuery = useDeferredValue(query);
  const deferredComponent = useDeferredValue(componentFilter);
  const { data: logsPage } = useLogsQuery({ pageSize: 75, search: deferredQuery });
  const logs = logsPage?.items ?? [];

  const filteredLogs = React.useMemo(() => {
    return logs.filter((entry) => {
      const queryMatch =
        deferredQuery.length === 0 ||
        `${entry.component} ${entry.message} ${entry.timestamp}`.toLowerCase().includes(deferredQuery.toLowerCase());
      const componentMatch =
        deferredComponent.length === 0 ||
        entry.component.toLowerCase().includes(deferredComponent.toLowerCase());
      return queryMatch && componentMatch;
    });
  }, [deferredComponent, deferredQuery, logs]);

  return (
    <div className="space-y-6">
      <div>
        <Typography variant="h4" className="font-display">
          Logs and Controls
        </Typography>
        <Typography variant="body2" className="max-w-3xl text-slate-400">
          This panel is for daemon reliability, poison-pill diagnostics, retention management, and operational tracing across projection and agent infrastructure.
        </Typography>
      </div>

      <SectionCard
        title="Operational Filters"
        subtitle="Search by stream activity, component, or retry condition."
      >
        <div className="grid gap-4 md:grid-cols-3">
          <TextField
            label="Search"
            placeholder="global_position, stream_id, poison pill"
            value={query}
            onChange={(event) => {
              const value = event.target.value;
              startTransition(() => setQuery(value));
            }}
            size="small"
          />
          <TextField
            label="Component"
            placeholder="projection-daemon"
            value={componentFilter}
            onChange={(event) => {
              const value = event.target.value;
              startTransition(() => setComponentFilter(value));
            }}
            size="small"
          />
          <TextField label="Retention (days)" defaultValue="30" size="small" />
        </div>
      </SectionCard>

      <SectionCard
        title="Recent Events"
        subtitle="Filtered view of projection daemon and agent runtime logs."
      >
        <div className="space-y-3">
          {filteredLogs.map((entry) => (
            <div key={entry.id} className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <Typography variant="subtitle2">{entry.component}</Typography>
                  <Typography variant="caption" className="text-slate-400">
                    {entry.timestamp}
                  </Typography>
                </div>
                <Chip label={entry.level} color={levelTone[entry.level]} size="small" />
              </div>
              <Typography variant="body2" className="mt-2 text-slate-300">
                {entry.message}
              </Typography>
            </div>
          ))}
          {filteredLogs.length === 0 ? (
            <Typography variant="body2" className="text-slate-400">
              No logs match the current filters.
            </Typography>
          ) : null}
        </div>
      </SectionCard>
    </div>
  );
};
