/**
 * GAP-025 (GAP-UI-03) ratchet: every console component and page keeps a sibling test.
 *
 * The gap was that all 45 files under `src/components/*.tsx` and `src/app/**\/page.tsx`
 * had zero direct coverage — the untested surface GAP-024 (GAP-UI-02) had been hiding in.
 * T082 added one `*.test.tsx` next to each, so the guard carries **no allowlist**: a new
 * component or page without a test fails here, which is the only thing stopping the count
 * drifting back up.
 *
 * Scope is GAP-025's own definition — components and route pages. Route infrastructure
 * (`app/layout.tsx`, `app/providers.tsx`, the nested `layout.tsx` files) and the two
 * non-route client components under `app/` (`login/LoginClient.tsx`, rendered and asserted
 * through `app/login/page.test.tsx`; `settings/discovery/WorkflowsAndValidation.tsx`, which
 * is not) are outside it. `app/layout.tsx` additionally cannot be rendered outside the Next
 * compiler because `next/font` is not callable there.
 *
 * The file list comes from the same walker `exports-documented.test.ts` uses rather than a
 * second directory walk; every component and page exports something, so every one of them
 * appears in its rows.
 */
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';

import { walkExports } from '../../../../specs/013-clean-code-arch-remediation/scripts/function_inventory_ts.mjs';

/** Posix-ish paths of the component and page files GAP-025 covers, from the walker's rows. */
function componentAndPageFiles(): string[] {
  const rows = walkExports('src') as { path: string }[];
  const paths = rows.map((row) => row.path.split('\\').join('/'));
  const matches = paths.filter(
    (p) =>
      !p.endsWith('.test.tsx') &&
      (/^src\/components\/[^/]+\.tsx$/.test(p) || /^src\/app\/(.+\/)?page\.tsx$/.test(p)),
  );
  return Array.from(new Set(matches)).sort();
}

describe('every console component and page has a sibling test', () => {
  it('finds the component and page files the walker reports', () => {
    const files = componentAndPageFiles();

    // Sanity: a walker that silently returned nothing would make the guard below vacuous.
    expect(files.length).toBeGreaterThanOrEqual(45);
    expect(files).toContain('src/components/AgentChat.tsx');
    expect(files).toContain('src/app/settings/migrate/page.tsx');
  });

  it('leaves no component or page without a `.test.tsx` beside it', () => {
    const untested = componentAndPageFiles().filter(
      (file) => !fs.existsSync(file.replace(/\.tsx$/, '.test.tsx')),
    );

    expect(untested).toEqual([]);
  });
});
