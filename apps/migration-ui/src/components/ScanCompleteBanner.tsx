'use client';

type ScanCompleteBannerProps = {
  message: string;
  onDismiss: () => void;
};

export function ScanCompleteBanner({ message, onDismiss }: ScanCompleteBannerProps) {
  return (
    <div className="scan-complete-banner" role="status">
      <span>{message}</span>
      <button
        type="button"
        className="scan-complete-banner__close"
        aria-label="Dismiss scan notification"
        onClick={onDismiss}
      >
        ×
      </button>
    </div>
  );
}
