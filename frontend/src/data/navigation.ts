import DashboardRounded from '@mui/icons-material/DashboardRounded';
import GavelRounded from '@mui/icons-material/GavelRounded';
import ShieldRounded from '@mui/icons-material/ShieldRounded';
import AdminPanelSettingsRounded from '@mui/icons-material/AdminPanelSettingsRounded';
import ReceiptLongRounded from '@mui/icons-material/ReceiptLongRounded';
import ScienceRounded from '@mui/icons-material/ScienceRounded';
import HistoryEduRounded from '@mui/icons-material/HistoryEduRounded';
import AccountCircleRounded from '@mui/icons-material/AccountCircleRounded';
import { Role } from '../auth/AuthProvider';

export type NavItem = {
  label: string;
  path: string;
  icon: React.ElementType;
  roles: Role[];
};

export const navItems: NavItem[] = [
  {
    label: 'Operations',
    path: '/dashboard',
    icon: DashboardRounded,
    roles: ['loan_officer', 'admin', 'user_proxy']
  },
  {
    label: 'Compliance',
    path: '/compliance',
    icon: GavelRounded,
    roles: ['compliance_officer', 'auditor', 'admin']
  },
  {
    label: 'Audit Trail',
    path: '/audit-trail',
    icon: HistoryEduRounded,
    roles: ['auditor', 'security_officer', 'admin']
  },
  {
    label: 'Security',
    path: '/security',
    icon: ShieldRounded,
    roles: ['security_officer', 'admin']
  },
  {
    label: 'Admin Metrics',
    path: '/admin',
    icon: AdminPanelSettingsRounded,
    roles: ['admin']
  },
  {
    label: 'What-If Studio',
    path: '/what-if',
    icon: ScienceRounded,
    roles: ['admin', 'auditor']
  },
  {
    label: 'Applicant Portal',
    path: '/applicant',
    icon: AccountCircleRounded,
    roles: ['applicant']
  },
  {
    label: 'Logs & Controls',
    path: '/logs',
    icon: ReceiptLongRounded,
    roles: ['security_officer', 'admin']
  }
];
