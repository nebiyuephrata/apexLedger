import React from 'react';
import {
  AppBar,
  Avatar,
  Box,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Toolbar,
  Tooltip,
  Typography
} from '@mui/material';
import MenuRounded from '@mui/icons-material/MenuRounded';
import NotificationsActiveRounded from '@mui/icons-material/NotificationsActiveRounded';
import AutoAwesomeRounded from '@mui/icons-material/AutoAwesomeRounded';
import { NavLink } from 'react-router-dom';
import { navItems } from '../data/navigation';
import { Role, useAuth } from '../auth/AuthProvider';
import { useActorProfilesQuery, useHealthQuery, useSessionQuery } from '../features/ledger/hooks';

const drawerWidth = 260;

const roleLabels: Record<Role, string> = {
  loan_officer: 'Loan Officer',
  compliance_officer: 'Compliance Officer',
  security_officer: 'Security Officer',
  admin: 'System Admin',
  auditor: 'Auditor',
  applicant: 'Applicant',
  user_proxy: 'UserProxy Agent'
};

export const AppShell: React.FC<React.PropsWithChildren> = ({ children }) => {
  const { user, setRole } = useAuth();
  const [mobileOpen, setMobileOpen] = React.useState(false);
  const [mode, setMode] = React.useState<'planning' | 'fast'>('planning');
  const { data: profiles } = useActorProfilesQuery();
  const { data: health } = useHealthQuery();
  const { data: session } = useSessionQuery();

  const handleDrawerToggle = () => setMobileOpen((prev) => !prev);

  const profile = React.useMemo(
    () => profiles?.find((entry) => entry.role === user?.role) ?? null,
    [profiles, user?.role],
  );
  const availableNav = React.useMemo(
    () =>
      navItems.filter((item) => {
        if (!user) return false;
        if (session?.allowed_views?.length) {
          const viewName = item.path.replace(/^\//, '');
          return session.allowed_views.includes(viewName);
        }
        return item.roles.includes(user.role);
      }),
    [session?.allowed_views, user],
  );

  const drawer = (
    <Box className="h-full bg-slate-950">
      <div className="p-6">
        <Typography variant="overline" className="text-slate-400">
          The Ledger
        </Typography>
        <Typography variant="h6" className="font-display text-white">
          CQRS Console
        </Typography>
      </div>
      <Divider className="border-slate-800" />
      <List className="px-3">
        {availableNav.map((item) => {
          const Icon = item.icon;
          return (
            <ListItemButton
              key={item.path}
              component={NavLink}
              to={item.path}
              className="rounded-xl my-1 text-slate-200 hover:bg-slate-900"
            >
              <ListItemIcon className="text-slate-400">
                <Icon fontSize="small" />
              </ListItemIcon>
              <ListItemText primary={item.label} />
            </ListItemButton>
          );
        })}
      </List>
      {!import.meta.env.VITE_CLERK_PUBLISHABLE_KEY ? (
        <>
          <Divider className="border-slate-800" />
          <div className="p-4">
            <Typography variant="caption" className="text-slate-400">
              Role Switcher (Local Dev)
            </Typography>
            <div className="mt-2 flex flex-wrap gap-2">
              {(Object.keys(roleLabels) as Role[]).map((role) => (
                <button
                  key={role}
                  onClick={() => setRole(role)}
                  className={`px-3 py-1 rounded-full text-xs border ${
                    user?.role === role
                      ? 'border-emerald-400 text-emerald-200'
                      : 'border-slate-700 text-slate-400'
                  }`}
                >
                  {roleLabels[role]}
                </button>
              ))}
            </div>
          </div>
          <Divider className="border-slate-800" />
        </>
      ) : (
        <Divider className="border-slate-800" />
      )}
      <div className="p-4 space-y-3">
        <Typography variant="caption" className="text-slate-400">
          Actor Context
        </Typography>
        <div className="rounded-2xl border border-slate-800 bg-slate-950/60 p-4">
          <Typography variant="subtitle2">{profile?.title ?? 'Loading role...'}</Typography>
          <Typography variant="body2" className="mt-2 text-slate-400">
            {profile?.focus ?? 'Resolving actor permissions and responsibilities.'}
          </Typography>
          <div className="mt-3 grid gap-2 text-xs text-slate-400">
            <div>Org: <span className="text-slate-200">{session?.org_id ?? 'unscoped'}</span></div>
            <div>Mode: <span className="text-slate-200">{session?.session_mode ?? 'interactive'}</span></div>
            <div>Identity: <span className="text-slate-200">{session?.identity_type ?? 'human'}</span></div>
          </div>
        </div>
        <div className="space-y-2">
          <Typography variant="caption" className="text-slate-500">
            Query Resources
          </Typography>
          {(profile?.resources ?? []).map((resource) => (
            <div key={resource} className="rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-2 text-xs text-slate-300">
              {resource}
            </div>
          ))}
        </div>
      </div>
    </Box>
  );

  return (
    <Box className="flex min-h-screen">
      <AppBar position="fixed" className="bg-slate-950/80 backdrop-blur border-b border-slate-800">
        <Toolbar className="flex justify-between">
          <div className="flex items-center gap-3">
            <IconButton color="inherit" edge="start" onClick={handleDrawerToggle} className="lg:hidden">
              <MenuRounded />
            </IconButton>
            <div>
              <Typography variant="overline" className="text-slate-400">
                Event-Sourced Operations
              </Typography>
              <Typography variant="h6" className="font-display text-white">
                {roleLabels[user?.role ?? 'loan_officer']}
              </Typography>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="hidden lg:flex items-center gap-2 rounded-full border border-slate-800 bg-slate-900/70 p-1">
              <button
                onClick={() => setMode('planning')}
                className={`rounded-full px-3 py-1 text-xs ${mode === 'planning' ? 'bg-emerald-400 text-slate-900' : 'text-slate-400'}`}
              >
                Planning
              </button>
              <button
                onClick={() => setMode('fast')}
                className={`rounded-full px-3 py-1 text-xs ${mode === 'fast' ? 'bg-emerald-400 text-slate-900' : 'text-slate-400'}`}
              >
                Fast
              </button>
            </div>
            <div className="hidden md:flex flex-col items-end">
              <Typography variant="caption" className="text-slate-400">
                Data Freshness
              </Typography>
              <Typography variant="body2" className="text-emerald-200">
                {health ? `App ${health.lags.application_summary?.lag_ms ?? 0}ms · Comp ${health.lags.compliance_audit?.lag_ms ?? 0}ms` : 'Loading lag'}
              </Typography>
            </div>
            <Tooltip title="Notifications">
              <IconButton color="inherit">
                <NotificationsActiveRounded />
              </IconButton>
            </Tooltip>
            <Avatar className="bg-emerald-400 text-slate-900" sx={{ width: 34, height: 34 }}>
              {user?.name?.slice(0, 1) ?? 'U'}
            </Avatar>
            <Tooltip title={`${mode} mode`}>
              <IconButton color="inherit">
                <AutoAwesomeRounded />
              </IconButton>
            </Tooltip>
          </div>
        </Toolbar>
      </AppBar>

      <Box component="nav" className="w-0 lg:w-[260px]">
        <Drawer
          variant="temporary"
          open={mobileOpen}
          onClose={handleDrawerToggle}
          ModalProps={{ keepMounted: true }}
          sx={{
            display: { xs: 'block', lg: 'none' },
            '& .MuiDrawer-paper': { width: drawerWidth, background: '#0b0c12' }
          }}
        >
          {drawer}
        </Drawer>
        <Drawer
          variant="permanent"
          open
          sx={{
            display: { xs: 'none', lg: 'block' },
            '& .MuiDrawer-paper': { width: drawerWidth, background: '#0b0c12', borderRight: '1px solid #1f2430' }
          }}
        >
          {drawer}
        </Drawer>
      </Box>

      <Box component="main" className="flex-1 px-6 pb-10 pt-24">
        {children}
      </Box>
    </Box>
  );
};
