import { redirect } from 'next/navigation';

/** Redirect a profile's index route to its source connection page. */
export default function ProfileIndexPage({ params }: { params: { profileId: string } }) {
  redirect(`/settings/profiles/${params.profileId}/source`);
}
