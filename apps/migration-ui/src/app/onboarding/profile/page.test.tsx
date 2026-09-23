/**
 * First-run onboarding mounts the profile wizard in onboarding mode (register id GAP-025). This is the
 * only route an admin can reach before any profile exists, so it must explain itself and
 * render the wizard's first step.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

import { renderMarkup, textOf } from '@/__tests__/renderMarkup';
import { resetNavState } from '@/__tests__/nextNavigationStub';

vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'));

import OnboardingProfilePage from './page';

afterEach(() => resetNavState());

describe('/onboarding/profile', () => {
  it('renders the onboarding wizard with masked credential inputs', () => {
    const html = renderMarkup(<OnboardingProfilePage />);
    const text = textOf(html);

    expect(text).toContain('Deployment profile setup');
    expect(text).toContain('First deployment profile');
    expect(html.match(/type="password"/g)).toHaveLength(2);
  });
});
