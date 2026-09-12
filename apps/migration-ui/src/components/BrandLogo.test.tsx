/**
 * GAP-025: render coverage for the brand mark.
 *
 * Sizing is passed through to the SVG rather than set in CSS, so a regression here changes
 * every header and the login card at once.
 */
import { describe, it, expect } from 'vitest';

import { renderMarkup, textOf } from '@/__tests__/renderMarkup';

import { BrandLogo, BrandWordmark } from './BrandLogo';

describe('BrandLogo', () => {
  it('renders an SVG at its default size', () => {
    const html = renderMarkup(<BrandLogo />);
    expect(html).toContain('<svg');
    expect(html).toContain('width="44"');
  });

  it('honours an explicit size and extra classes', () => {
    const html = renderMarkup(<BrandLogo size={16} className="nav-logo" />);
    expect(html).toContain('width="16"');
    expect(html).toContain('height="16"');
    expect(html).toContain('nav-logo');
  });
});

describe('BrandWordmark', () => {
  it('names the product inline by default', () => {
    expect(textOf(renderMarkup(<BrandWordmark />))).toContain('ADO2GitHub');
  });

  it('renders the stacked layout on request', () => {
    const inline = renderMarkup(<BrandWordmark />);
    const stacked = renderMarkup(<BrandWordmark layout="stacked" />);

    expect(stacked).not.toBe(inline);
    expect(textOf(stacked)).toContain('ADO2GitHub');
  });
});
