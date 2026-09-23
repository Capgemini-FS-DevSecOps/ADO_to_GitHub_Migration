import { redirect } from 'next/navigation';

/** Redirect the legacy /migrate route to the migration settings page. */
export default function MigratePage() {
  redirect('/settings/migrate');
}
