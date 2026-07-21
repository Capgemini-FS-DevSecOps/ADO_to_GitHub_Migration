type BrandLogoProps = {
  size?: number;
  className?: string;
};

type BrandWordmarkProps = {
  layout?: 'inline' | 'stacked';
  className?: string;
};

/** Migration flow mark — ADO node → GitHub node, OrchestrateAI cyan palette */
export function BrandLogo({ size = 44, className = '' }: BrandLogoProps) {
  const id = 'brand-grad';
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={`brand-logo-mark ${className}`.trim()}
      aria-hidden
    >
      <defs>
        <linearGradient id={id} x1="4" y1="4" x2="44" y2="44" gradientUnits="userSpaceOnUse">
          <stop stopColor="#35B8FF" />
          <stop offset="1" stopColor="#1A8ACC" />
        </linearGradient>
      </defs>
      <rect x="1" y="1" width="46" height="46" rx="13" fill="rgba(53,184,255,0.08)" stroke={`url(#${id})`} strokeWidth="1" />
      <circle cx="15" cy="24" r="4.5" fill={`url(#${id})`} opacity="0.9" />
      <circle cx="33" cy="24" r="4.5" stroke="#35B8FF" strokeWidth="1.5" fill="none" />
      <path
        d="M20 24h8M26 20l4 4-4 4"
        stroke="#35B8FF"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M24 14v4M24 30v4"
        stroke="#35B8FF"
        strokeWidth="1.25"
        strokeLinecap="round"
        opacity="0.45"
      />
    </svg>
  );
}

export function BrandWordmark({ layout = 'inline', className = '' }: BrandWordmarkProps) {
  return (
    <div className={`oai-brand-wordmark oai-brand-wordmark--${layout} ${className}`.trim()}>
      <span className="oai-brand-product">ADO2GitHub</span>
      {layout === 'inline' ? <span className="oai-brand-divider" aria-hidden /> : null}
      <span className="oai-brand-suite">Migration Console</span>
    </div>
  );
}
