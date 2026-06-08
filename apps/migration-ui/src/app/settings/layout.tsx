import { SettingsNav } from '@/components/SettingsNav';

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <h1 className="oai-page-title">Settings</h1>
      <SettingsNav />
      {children}
    </div>
  );
}
