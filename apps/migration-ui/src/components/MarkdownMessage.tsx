'use client';

import Link from 'next/link';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';

type MarkdownMessageProps = {
  content: string;
  className?: string;
};

const markdownComponents: Components = {
  a({ href, children, ...props }) {
    if (href && href.startsWith('/')) {
      return (
        <Link href={href} {...props}>
          {children}
        </Link>
      );
    }
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
        {children}
      </a>
    );
  },
};

export function MarkdownMessage({ content, className }: MarkdownMessageProps) {
  return (
    <div className={className ? `agent-markdown ${className}` : 'agent-markdown'}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
