import { redirect } from 'next/navigation';

/** Redirect the settings index to the migration profiles page. */
export default function SettingsIndexPage() {
  redirect('/settings/profiles');
}
