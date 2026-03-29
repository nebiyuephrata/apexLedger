import React from 'react';
import { Button, Chip, Typography } from '@mui/material';
import toast from 'react-hot-toast';
import { SectionCard } from '../components/SectionCard';
import { useApplicationsQuery } from '../features/ledger/hooks';

const currency = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
});

export const Applicant: React.FC = () => {
  const { data: applicationsPage } = useApplicationsQuery();
  const applications = applicationsPage?.items ?? [];
  const application = applications[0] ?? null;

  return (
    <div className="space-y-6">
      <div>
        <Typography variant="h4" className="font-display">
          Applicant Portal
        </Typography>
        <Typography variant="body2" className="max-w-3xl text-slate-400">
          Applicants only see projection-backed status and submission progress. They do not get direct event-log access.
        </Typography>
      </div>

      <SectionCard
        title="Current Application"
        subtitle="Submission progress and document intake status."
        actions={<Button variant="contained" onClick={() => toast.success('Document upload handoff queued')}>Upload Documents</Button>}
      >
        {application ? (
          <div className="space-y-4">
            <div className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
              <Typography variant="overline" className="text-slate-400">
                {application.application_id}
              </Typography>
              <Typography variant="h5" className="font-display">
                {currency.format(application.requested_amount_usd ?? 0)}
              </Typography>
              <div className="mt-3 flex flex-wrap gap-2">
                <Chip label={application.state ?? 'UNKNOWN'} color="info" />
                <Chip label={application.compliance_status ?? 'PENDING'} variant="outlined" />
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                <Typography variant="caption" className="text-slate-400">
                  Last Event
                </Typography>
                <Typography variant="body1">{application.last_event_type ?? 'Pending'}</Typography>
              </div>
              <div className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
                <Typography variant="caption" className="text-slate-400">
                  Decision Recommendation
                </Typography>
                <Typography variant="body1">{application.decision_recommendation ?? 'Under review'}</Typography>
              </div>
            </div>
          </div>
        ) : (
          <Typography variant="body2" className="text-slate-400">
            Loading application status...
          </Typography>
        )}
      </SectionCard>
    </div>
  );
};
