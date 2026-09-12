/**
 * Server-render helpers for the component and page tests added under GAP-025.
 *
 * The console has no DOM test environment installed — no `jsdom`/`happy-dom`, no
 * `@testing-library/*` — and FR-008/SC-003 forbid adding one, so these tests render with
 * `react-dom/server`, which is already a runtime dependency, and assert on the markup a
 * user would see. Effects and event handlers do not run under `renderToStaticMarkup`, so
 * what these tests pin is *first paint*: safety defaults, permission and loading gates,
 * and prop-driven output. Interaction coverage stays out of reach until an operator
 * approves a DOM testing dependency.
 */
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';

/**
 * Render `node` to static HTML inside a fresh react-query client.
 *
 * Every test gets its own `QueryClient` so no cached query result leaks between tests;
 * retries are off so a component that queries during render fails fast instead of hanging.
 */
export function renderMarkup(node: ReactNode): string {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return renderToStaticMarkup(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

/** The visible text of rendered markup: tags stripped, whitespace collapsed. */
export function textOf(html: string): string {
  return html
    .replace(/<[^>]*>/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&#x27;|&apos;/g, "'")
    .replace(/\s+/g, ' ')
    .trim();
}
