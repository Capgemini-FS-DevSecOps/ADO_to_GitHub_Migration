'use client';

import { useEffect } from 'react';

/**
 * Detects iframe embedding and viewport size so CSS can adapt (platform embeds, narrow panels).
 */
export function EmbedLayout({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    const root = document.documentElement;

    const syncLayout = () => {
      const embedded = window.self !== window.top;
      root.classList.toggle('is-embedded', embedded);
      root.style.setProperty('--app-width', `${root.clientWidth}px`);
      root.style.setProperty('--app-height', `${root.clientHeight}px`);
    };

    syncLayout();
    window.addEventListener('resize', syncLayout);

    const observer = new ResizeObserver(syncLayout);
    observer.observe(root);

    return () => {
      window.removeEventListener('resize', syncLayout);
      observer.disconnect();
    };
  }, []);

  return <>{children}</>;
}
