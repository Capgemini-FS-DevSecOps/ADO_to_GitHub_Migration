import { redirect } from 'next/navigation';

/** Redirect the legacy /discovery route to the discovery settings page. */
export default function DiscoveryPage() {
  redirect('/settings/discovery');
}
