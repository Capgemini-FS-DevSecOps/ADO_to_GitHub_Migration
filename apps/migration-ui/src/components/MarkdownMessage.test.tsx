/**
 * GAP-025: render coverage for agent message markdown.
 *
 * Agent output is model-generated text rendered into the operator's console. The property
 * that matters is that it is rendered as markdown and *not* as HTML: raw tags in a model
 * response must come out as visible text, never as live markup (Principle V).
 */
import { describe, it, expect } from 'vitest';

import { renderMarkup, textOf } from '@/__tests__/renderMarkup';

import { MarkdownMessage } from './MarkdownMessage';

describe('MarkdownMessage', () => {
  it('renders markdown emphasis and code as elements', () => {
    const html = renderMarkup(<MarkdownMessage content={'**bold** and `code`'} />);

    expect(html).toContain('<strong>bold</strong>');
    expect(html).toContain('<code>code</code>');
  });

  it('renders GitHub-flavoured tables', () => {
    const html = renderMarkup(
      <MarkdownMessage content={'| repo | risk |\n| --- | --- |\n| svc-a | low |'} />,
    );

    expect(html).toContain('<table>');
    expect(textOf(html)).toContain('svc-a');
  });

  it('escapes raw HTML in a model response instead of rendering it', () => {
    const html = renderMarkup(
      <MarkdownMessage content={'<img src="x" onerror="alert(1)"> plain'} />,
    );

    expect(html).not.toContain('<img');
    expect(html).toContain('&lt;img'); // escaped to text, so the onerror handler never runs
    expect(textOf(html)).toContain('plain');
  });

  it('accepts an extra class name', () => {
    expect(renderMarkup(<MarkdownMessage content="hi" className="agent-bubble" />)).toContain(
      'agent-bubble',
    );
  });
});
