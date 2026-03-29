import React from 'react';
import { Button, Card, CardContent, Typography } from '@mui/material';
import { useNavigate } from 'react-router-dom';

export const Unauthorized: React.FC = () => {
  const navigate = useNavigate();
  return (
    <div className="min-h-[70vh] flex items-center justify-center">
      <Card className="bg-slate-900/70 border border-slate-800 max-w-md w-full">
        <CardContent className="text-center space-y-3">
          <Typography variant="h5" className="font-display">
            Access Denied
          </Typography>
          <Typography variant="body2" className="text-slate-400">
            Your role does not allow access to this view.
          </Typography>
          <Button variant="outlined" onClick={() => navigate('/')}>
            Return to Allowed View
          </Button>
        </CardContent>
      </Card>
    </div>
  );
};
