import React from 'react';
import { Button, Card, CardContent, Typography } from '@mui/material';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';

export const Login: React.FC = () => {
  const navigate = useNavigate();
  const { user } = useAuth();

  React.useEffect(() => {
    if (user) {
      navigate('/');
    }
  }, [user, navigate]);

  return (
    <div className="min-h-[70vh] flex items-center justify-center">
      <Card className="bg-slate-900/70 border border-slate-800 max-w-md w-full">
        <CardContent className="text-center space-y-3">
          <Typography variant="h5" className="font-display">
            Ledger Access Gateway
          </Typography>
          <Typography variant="body2" className="text-slate-400">
            Use Clerk SSO in production. Mock access is enabled in this environment.
          </Typography>
          <Button variant="contained" onClick={() => navigate('/')}>
            Enter Console
          </Button>
        </CardContent>
      </Card>
    </div>
  );
};
