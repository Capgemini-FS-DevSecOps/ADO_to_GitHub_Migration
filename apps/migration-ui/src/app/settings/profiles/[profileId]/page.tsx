import { redirect } from 'next/navigation';

export default function ProfileIndexPage({ params }: { params: { profileId: string } }) {
  redirect(`/settings/profiles/${params.profileId}/source`);
}
