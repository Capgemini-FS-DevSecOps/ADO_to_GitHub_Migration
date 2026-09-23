import { redirect } from 'next/navigation';

/** Redirect the legacy /agent route to the agent chat under settings. */
export default function AgentPage() {
  redirect('/settings/agent');
}
