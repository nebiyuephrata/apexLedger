import React from 'react';
import { Alert, Button, Card, CardContent, MenuItem, Stack, TextField, Typography } from '@mui/material';
import { useNavigate } from 'react-router-dom';
import { Role, useAuth } from '../auth/AuthProvider';

const roles: Role[] = [
  'loan_officer',
  'compliance_officer',
  'security_officer',
  'admin',
  'auditor',
  'applicant',
  'user_proxy',
];

export const Login: React.FC = () => {
  const navigate = useNavigate();
  const { user, signIn } = useAuth();
  const [role, setRole] = React.useState<Role>('loan_officer');
  const [orgId, setOrgId] = React.useState(import.meta.env.VITE_LEDGER_DEV_ORG_ID ?? 'org_demo');
  const [password, setPassword] = React.useState('');
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (user) {
      navigate('/');
    }
  }, [user, navigate]);

  const submit = async () => {
    try {
      setError(null);
      await signIn?.({ role, password, orgId });
      navigate('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to sign in.');
    }
  };

  return (
    <div className="min-h-[70vh] flex items-center justify-center px-4">
      <Card className="bg-slate-900/70 border border-slate-800 max-w-md w-full">
        <CardContent className="space-y-4">
          <div className="text-center space-y-2">
            <Typography variant="h5" className="font-display">
              Ledger Access Gateway
            </Typography>
            <Typography variant="body2" className="text-slate-400">
              Use Clerk SSO in production. In local development, choose a role and use the shared dummy password.
            </Typography>
          </div>

          {error ? <Alert severity="error">{error}</Alert> : null}

          <Stack spacing={2}>
            <TextField select label="Role" value={role} onChange={(event) => setRole(event.target.value as Role)} fullWidth>
              {roles.map((item) => (
                <MenuItem key={item} value={item}>
                  {item.replace(/_/g, ' ')}
                </MenuItem>
              ))}
            </TextField>
            <TextField label="Organization" value={orgId} onChange={(event) => setOrgId(event.target.value)} fullWidth />
            <TextField label="Password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} fullWidth />
          </Stack>

          <Button variant="contained" fullWidth onClick={submit}>
            Enter Console
          </Button>
        </CardContent>
      </Card>
    </div>
  );
};
