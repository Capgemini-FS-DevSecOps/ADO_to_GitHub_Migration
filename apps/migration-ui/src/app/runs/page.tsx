import { Suspense } from 'react';
import MonitorClient from './MonitorClient';

export default function RunsPage() {
  return (
    <Suspense
      fallback={
        <div className="oai-loading">
          <div className="oai-spinner" />
        </div>
      }
    >
      <MonitorClient />
    </Suspense>
  );
}
