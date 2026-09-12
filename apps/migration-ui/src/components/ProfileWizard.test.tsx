/**
 * GAP-025: render coverage for the profile wizard.
 *
 * This is the one form in the console where an operator types both an ADO PAT and a GitHub
 * PAT. Both must be `type="password"` so the credential is never echoed on screen
 * (Principle V, CA-003), and the wizard must open on its first step with the title matching
 * the mode it was mounted in.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { renderMarkup, textOf } from '@/__tests__/renderMarkup';
import { resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import { ProfileWizard } from './ProfileWizard';

afterEach(() => resetNavState());

describe('ProfileWizard', () => {
  it('opens on the source step with all three steps listed', () => {
    const text = textOf(renderMarkup(<ProfileWizard />));

    expect(text).toContain('New migration profile');
    expect(text).toContain('Source (ADO)');
    expect(text).toContain('Target (GitHub)');
    expect(text).toContain('Confirm');
    expect(text).toContain('Connect to your ADO organization');
  });

  it('titles itself for first-run onboarding', () => {
    expect(textOf(renderMarkup(<ProfileWizard mode="onboarding" />))).toContain(
      'First deployment profile',
    );
  });

  it('masks both credential inputs', () => {
    const html = renderMarkup(<ProfileWizard />);

    expect(html).toContain('id="ado_pat"');
    expect(html).toContain('id="github_token"');
    expect(html.match(/type="password"/g)).toHaveLength(2);
    expect(html).not.toContain('id="ado_pat" type="text"');
  });
});
