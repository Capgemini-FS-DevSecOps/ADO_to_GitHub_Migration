import { redirect } from 'next/navigation';

/** Redirect the legacy /dashboard route to the console home dashboard. */
export default function DashboardRedirectPage() {
  redirect('/');
}
