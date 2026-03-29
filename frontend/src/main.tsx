import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { CssBaseline, ThemeProvider } from '@mui/material';
import { Toaster } from 'react-hot-toast';
import App from './App';
import { authProvider } from './auth/AuthProvider';
import { theme } from './theme';
import { queryClient } from './platform/queryClient';
import { AppErrorBoundary } from './components/AppErrorBoundary';
import './index.css';

const Root = authProvider(({ children }) => (
  <ThemeProvider theme={theme}>
    <CssBaseline />
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    <Toaster position="top-right" />
  </ThemeProvider>
));

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Root>
      <AppErrorBoundary>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </AppErrorBoundary>
    </Root>
  </React.StrictMode>
);
