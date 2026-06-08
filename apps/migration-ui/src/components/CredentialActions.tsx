'use client';

import type { ReactNode } from 'react';

type ValidateButtonProps = {
  onClick: () => void;
  loading?: boolean;
  label?: string;
  title?: string;
};

export function ValidateButton({
  onClick,
  loading = false,
  label = 'Test connection',
  title = 'Validate credential',
}: ValidateButtonProps) {
  return (
    <button
      type="button"
      className="validate-btn"
      onClick={onClick}
      disabled={loading}
      title={title}
    >
      {loading ? (
        <>
          <span className="validate-btn-spinner" aria-hidden />
          Validating…
        </>
      ) : (
        label
      )}
    </button>
  );
}

export function IconButton({
  onClick,
  title,
  children,
  variant = 'default',
  disabled,
}: {
  onClick: () => void;
  title: string;
  children: ReactNode;
  variant?: 'default' | 'danger';
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className={`icon-btn${variant === 'danger' ? ' icon-btn-danger' : ''}`}
      onClick={onClick}
      title={title}
      aria-label={title}
      disabled={disabled}
    >
      {children}
    </button>
  );
}

export function ValidationResult({
  valid,
  message,
  warnings,
  scopes,
}: {
  valid: boolean;
  message: string;
  warnings?: string[];
  scopes?: string[];
}) {
  return (
    <div className={`validation-result${valid ? ' validation-result-ok' : ' validation-result-fail'}`}>
      <p>{message}</p>
      {scopes && scopes.length > 0 && (
        <p className="validation-scopes">Scopes: {scopes.join(', ')}</p>
      )}
      {warnings?.map((w) => (
        <p key={w} className="validation-warning">
          {w}
        </p>
      ))}
    </div>
  );
}

export function EditIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
      <path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  );
}

export function TrashIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  );
}
