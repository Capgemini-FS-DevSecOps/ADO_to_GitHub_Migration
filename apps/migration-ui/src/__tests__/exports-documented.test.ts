import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// The walker is a feature artefact of spec 013, not console source, so it is imported
// by relative path (contracts/artifact-schemas.md: the vitest guard imports the walker).
import { walkExports } from '../../../../specs/013-clean-code-arch-remediation/scripts/function_inventory_ts.mjs';

/**
 * One inventory row (data-model § FunctionInventoryEntry). The walker is plain `.mjs`
 * whose JSDoc declares only `@returns {object[]}`, so the fields it actually writes
 * (`function_inventory_ts.mjs` `push()`) are named here and verified by `toRows` at
 * runtime — a schema drift in the walker fails this guard instead of the type check.
 */
interface InventoryRow {
  id: string;
  language: string;
  path: string;
  line: number;
  qualname: string;
  signature: string;
  is_export: boolean;
  param_count: number;
  tags: string[];
  proposed_tags: string[];
  protected: boolean;
  documented: boolean;
  next_route_export: boolean;
}

const ROW_KEYS: (keyof InventoryRow)[] = [
  'id',
  'language',
  'path',
  'line',
  'qualname',
  'signature',
  'is_export',
  'param_count',
  'tags',
  'proposed_tags',
  'protected',
  'documented',
  'next_route_export',
];

/** Narrow `walkExports` output to `InventoryRow[]`, throwing if a row lacks a documented field. */
function toRows(value: unknown): InventoryRow[] {
  if (!Array.isArray(value)) throw new Error(`walkExports did not return an array: ${typeof value}`);
  return (value as unknown[]).map((entry) => {
    if (typeof entry !== 'object' || entry === null) {
      throw new Error(`walkExports row is not an object: ${String(entry)}`);
    }
    const missing = ROW_KEYS.filter((key) => !(key in entry));
    if (missing.length) throw new Error(`walkExports row is missing: ${missing.join(', ')}`);
    return entry as InventoryRow;
  });
}

/** The row matching `match`, asserted present so callers see `InventoryRow`, not `| undefined`. */
function requireRow(rows: InventoryRow[], match: (row: InventoryRow) => boolean): InventoryRow {
  const row = rows.find(match);
  expect(row).toBeDefined();
  if (!row) throw new Error('walkExports emitted no matching row');
  return row;
}

let root: string;

const COMPONENT_TSX = `import { useState } from 'react';

/**
 * Render the repo card.
 */
export const RepoCard = ({ name }: { name: string }) => {
  const [open, setOpen] = useState(false);
  const onToggle = () => setOpen((v) => !v);
  return { name, open, onToggle };
};

export function undocumentedHelper(a: string) {
  return a;
}
`;

const PAGE_TSX = `export default function DashboardPage() {
  return null;
}
`;

beforeAll(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'inv-ts-'));
  fs.mkdirSync(path.join(root, 'components'), { recursive: true });
  fs.mkdirSync(path.join(root, 'app', 'dashboard'), { recursive: true });
  fs.writeFileSync(path.join(root, 'components', 'RepoCard.tsx'), COMPONENT_TSX, 'utf8');
  fs.writeFileSync(path.join(root, 'app', 'dashboard', 'page.tsx'), PAGE_TSX, 'utf8');
});

afterAll(() => {
  fs.rmSync(root, { recursive: true, force: true });
});

describe('walkExports', () => {
  it('marks an exported arrow-function component with a leading JSDoc block as documented', () => {
    const rows = toRows(walkExports(root));
    const row = requireRow(rows, (r) => r.qualname === 'RepoCard');
    expect(row.documented).toBe(true);
    expect(row.tags).not.toContain('missing_docstring');
  });

  it('marks an undocumented export function as undocumented', () => {
    const rows = toRows(walkExports(root));
    const row = requireRow(rows, (r) => r.qualname === 'undocumentedHelper');
    expect(row.documented).toBe(false);
    expect(row.tags).toContain('missing_docstring');
  });

  it('does not emit a row for an inline callback inside a component', () => {
    const rows = toRows(walkExports(root));
    expect(rows.some((r) => r.qualname === 'onToggle')).toBe(false);
    expect(rows.every((r) => r.is_export === true)).toBe(true);
  });

  it('marks the default export of app/**/page.tsx as a Next.js route export', () => {
    const rows = toRows(walkExports(root));
    const row = requireRow(rows, (r) => r.path.endsWith('app/dashboard/page.tsx'));
    expect(row.qualname).toBe('default');
    expect(row.next_route_export).toBe(true);
  });
});

// T073 (research R4): the zero-`missing_docstring` state the cleanup reached is enforced
// here, not just measured in inventory.json. `walkExports` reads `src` relative to the
// cwd, which `vitest run` sets to `apps/migration-ui` (package.json `test` script); a
// wrong cwd makes the walk throw ENOENT rather than pass on an empty set.
describe('every export in the console is documented', () => {
  it('finds no export under src/ without a leading JSDoc block', () => {
    const rows = toRows(walkExports('src'));
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.filter((row) => !row.documented).map((row) => row.id)).toEqual([]);
  });
});
