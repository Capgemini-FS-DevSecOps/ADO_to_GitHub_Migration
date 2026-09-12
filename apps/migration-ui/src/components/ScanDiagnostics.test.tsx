/**
 * GAP-025: render coverage for scan diagnostics.
 *
 * "Scanned N projects, found 0 repos" is the shape of a silently-failed discovery run, and
 * this component is the only place the operator is told about it — both through the
 * explicit `status: 'empty'` and through the inferred case of projects but no repos.
 */
import { describe, it, expect } from 'vitest';

import type { ScanProjectDetail } from '@/lib/types';
import { renderMarkup, textOf } from '@/__tests__/renderMarkup';

import { ScanDiagnostics } from './ScanDiagnostics';

const projects: ScanProjectDetail[] = [
  { project: 'Payments', project_id: 'p1', repo_count: 3, disabled_count: 1, pipeline_count: 7 },
  { project: 'Billing', project_id: 'p2', repo_count: 0 },
  { project: 'Legacy', project_id: 'p3', repo_count: 0, error: 'HTTP 403' },
];

describe('ScanDiagnostics', () => {
  it('summarises the project and repo counts', () => {
    const text = textOf(renderMarkup(<ScanDiagnostics projectsScanned={3} reposScanned={12} />));

    expect(text).toContain('3 project(s)');
    expect(text).toContain('12 Git repo(s) found');
    expect(text).not.toContain('No repos discovered');
  });

  it('flags a scan that walked projects but found no repositories', () => {
    const text = textOf(renderMarkup(<ScanDiagnostics projectsScanned={3} reposScanned={0} />));
    expect(text).toContain('No repos discovered');
  });

  it('flags a scan the server itself reported as empty', () => {
    const text = textOf(
      renderMarkup(<ScanDiagnostics projectsScanned={0} reposScanned={0} status="empty" />),
    );
    expect(text).toContain('No repos discovered');
  });

  it('lists every warning the scan returned', () => {
    const text = textOf(
      renderMarkup(
        <ScanDiagnostics
          projectsScanned={1}
          reposScanned={1}
          warnings={['PAT lacks Code (read)', 'TFVC projects skipped']}
        />,
      ),
    );

    expect(text).toContain('PAT lacks Code (read)');
    expect(text).toContain('TFVC projects skipped');
  });

  it('shows per-project status, preferring an error over the OK verdict', () => {
    const text = textOf(
      renderMarkup(
        <ScanDiagnostics projectsScanned={3} reposScanned={3} projectDetails={projects} />,
      ),
    );

    expect(text).toContain('Payments 3 1 7 OK');
    expect(text).toContain('Billing 0 0 0 No Git repos');
    expect(text).toContain('Legacy 0 0 0 HTTP 403');
  });

  it('omits the project table when the scan returned no per-project detail', () => {
    const html = renderMarkup(
      <ScanDiagnostics projectsScanned={1} reposScanned={1} projectDetails={[]} />,
    );
    expect(html).not.toContain('scan-project-table');
  });
});
