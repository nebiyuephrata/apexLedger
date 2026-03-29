import { createTheme } from '@mui/material/styles';

export const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: { main: '#f05d23' },
    secondary: { main: '#48fbd5' },
    background: {
      default: '#07080b',
      paper: '#12131a'
    },
    success: { main: '#5af07f' },
    warning: { main: '#fca311' },
    error: { main: '#ff6b6b' }
  },
  typography: {
    fontFamily: 'IBM Plex Sans, system-ui, sans-serif',
    h3: {
      fontFamily: 'Space Grotesk, system-ui, sans-serif',
      fontWeight: 700
    },
    h4: {
      fontFamily: 'Space Grotesk, system-ui, sans-serif',
      fontWeight: 600
    },
    h5: {
      fontFamily: 'Space Grotesk, system-ui, sans-serif',
      fontWeight: 600
    }
  },
  shape: {
    borderRadius: 14
  }
});
