import React from 'react';
import { Button, Typography } from '@mui/material';

export class AppErrorBoundary extends React.Component<React.PropsWithChildren, { hasError: boolean; message?: string }> {
  constructor(props: React.PropsWithChildren) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, message: error.message };
  }

  override componentDidCatch(error: Error) {
    console.error('Ledger UI crashed', error);
  }

  override render() {
    if (this.state.hasError) {
      return (
        <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-slate-950 px-6 text-center text-slate-100">
          <Typography variant="h4">The Ledger hit a UI fault</Typography>
          <Typography variant="body1" className="max-w-xl text-slate-400">
            We kept the error isolated so the rest of the app is safe. Refresh to retry, and share the screen with support if it keeps happening.
          </Typography>
          <Typography variant="caption" className="text-slate-500">
            {this.state.message}
          </Typography>
          <Button variant="contained" onClick={() => window.location.reload()}>
            Reload Console
          </Button>
        </div>
      );
    }
    return this.props.children;
  }
}
