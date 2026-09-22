/**
 * This test provides render coverage for the embed layout wrapper (register id GAP-025).
 *
 * All of its work happens in an effect against `document`, so the property a server render
 * can pin — and the one that matters for the platform embed — is that it is transparent:
 * children render unwrapped, with no element of its own.
 */
import { describe, it, expect } from 'vitest';

import { renderMarkup } from '@/__tests__/renderMarkup';

import { EmbedLayout } from './EmbedLayout';

describe('EmbedLayout', () => {
  it('renders its children without adding a wrapper element', () => {
    const html = renderMarkup(
      <EmbedLayout>
        <main id="console">body</main>
      </EmbedLayout>,
    );

    expect(html).toBe('<main id="console">body</main>');
  });
});
