/**
 * This test provides render coverage for the credential action controls (register id GAP-025).
 *
 * These sit on every token and cloud-credential form. The disabled-while-validating state
 * is the only thing stopping an operator firing a second validation call over the first,
 * and `IconButton` is the console's only icon-only control, so its accessible name comes
 * from `title` rather than from any visible text.
 */
import { describe, it, expect } from 'vitest';

import { renderMarkup, textOf } from '@/__tests__/renderMarkup';

import {
  EditIcon,
  IconButton,
  TrashIcon,
  ValidateButton,
  ValidationResult,
} from './CredentialActions';

const noop = () => {};

describe('ValidateButton', () => {
  it('offers the default label and stays enabled when idle', () => {
    const html = renderMarkup(<ValidateButton onClick={noop} />);

    expect(textOf(html)).toBe('Test connection');
    expect(html).not.toContain('disabled');
    expect(html).toContain('title="Validate credential"');
  });

  it('disables itself and says it is working while a validation is in flight', () => {
    const html = renderMarkup(<ValidateButton onClick={noop} loading />);

    expect(html).toContain('disabled');
    expect(textOf(html)).toContain('Validating');
    expect(textOf(html)).not.toContain('Test connection');
  });

  it('uses a caller-supplied label', () => {
    expect(textOf(renderMarkup(<ValidateButton onClick={noop} label="Re-test" />))).toBe('Re-test');
  });
});

describe('IconButton', () => {
  it('takes its accessible name from the title', () => {
    const html = renderMarkup(
      <IconButton onClick={noop} title="Delete token">
        <TrashIcon />
      </IconButton>,
    );

    expect(html).toContain('aria-label="Delete token"');
    expect(html).toContain('<svg');
    expect(html).not.toContain('icon-btn-danger');
  });

  it('marks the danger variant and honours disabled', () => {
    const html = renderMarkup(
      <IconButton onClick={noop} title="Delete" variant="danger" disabled>
        <EditIcon />
      </IconButton>,
    );

    expect(html).toContain('icon-btn-danger');
    expect(html).toContain('disabled');
  });
});

describe('ValidationResult', () => {
  it('reports a successful validation with its granted scopes', () => {
    const html = renderMarkup(
      <ValidationResult valid message="Token accepted" scopes={['repo', 'workflow']} />,
    );

    expect(html).toContain('validation-result-ok');
    expect(textOf(html)).toContain('Token accepted');
    expect(textOf(html)).toContain('Scopes: repo, workflow');
  });

  it('reports a failure and lists every warning', () => {
    const html = renderMarkup(
      <ValidationResult
        valid={false}
        message="Token rejected"
        warnings={['Missing workflow scope', 'Expires in 3 days']}
      />,
    );

    expect(html).toContain('validation-result-fail');
    expect(textOf(html)).toContain('Missing workflow scope');
    expect(textOf(html)).toContain('Expires in 3 days');
  });

  it('omits the scopes line when the caller passes none', () => {
    const html = renderMarkup(<ValidationResult valid message="ok" scopes={[]} />);
    expect(html).not.toContain('validation-scopes');
  });
});
