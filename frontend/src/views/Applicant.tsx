import React from 'react';
import { Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, TextField, Typography } from '@mui/material';
import toast from 'react-hot-toast';
import { SectionCard } from '../components/SectionCard';
import { useApplicationsQuery, useUploadDocumentMutation } from '../features/ledger/hooks';
import { LedgerApiError } from '../lib/ledgerClient';

const currency = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
});

export const Applicant: React.FC = () => {
  const [uploadOpen, setUploadOpen] = React.useState(false);
  const [documentType, setDocumentType] = React.useState('income_statement');
  const [fiscalYear, setFiscalYear] = React.useState('2025');
  const [file, setFile] = React.useState<File | null>(null);
  const { data: applicationsPage } = useApplicationsQuery();
  const uploadDocument = useUploadDocumentMutation();
  const applications = applicationsPage?.items ?? [];
  const application = applications[0] ?? null;

  const submitUpload = () => {
    if (!application) {
      toast.error('Create or select an application first.');
      return;
    }
    if (!file) {
      toast.error('Choose a file to upload.');
      return;
    }
    uploadDocument.mutate(
      {
        applicationId: application.application_id,
        documentType,
        fiscalYear,
        file,
      },
      {
        onSuccess: (result) => {
          toast.success(`Uploaded ${result.filename}`);
          setUploadOpen(false);
          setFile(null);
        },
        onError: (error) => {
          if (error instanceof LedgerApiError) {
            toast.error(error.message);
            return;
          }
          toast.error(error instanceof Error ? error.message : 'Upload failed.');
        },
      },
    );
  };

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
        actions={<Button variant="contained" onClick={() => setUploadOpen(true)}>Upload Documents</Button>}
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

      <Dialog open={uploadOpen} onClose={() => setUploadOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>Upload Application Document</DialogTitle>
        <DialogContent className="space-y-4">
          <TextField
            select
            margin="dense"
            label="Document Type"
            fullWidth
            value={documentType}
            onChange={(event) => setDocumentType(event.target.value)}
          >
            <MenuItem value="income_statement">Income Statement</MenuItem>
            <MenuItem value="balance_sheet">Balance Sheet</MenuItem>
            <MenuItem value="cash_flow_statement">Cash Flow Statement</MenuItem>
          </TextField>
          <TextField
            margin="dense"
            label="Fiscal Year"
            fullWidth
            value={fiscalYear}
            onChange={(event) => setFiscalYear(event.target.value)}
          />
          <Button component="label" variant="outlined">
            {file ? file.name : 'Choose File'}
            <input
              hidden
              type="file"
              accept=".pdf,.xlsx,.xls,.csv"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </Button>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setUploadOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={submitUpload} disabled={uploadDocument.isPending}>
            Upload
          </Button>
        </DialogActions>
      </Dialog>
    </div>
  );
};
