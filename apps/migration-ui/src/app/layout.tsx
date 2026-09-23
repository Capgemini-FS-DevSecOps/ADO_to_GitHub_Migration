import type { Metadata, Viewport } from 'next';
import { Inter } from 'next/font/google';
import { Providers } from './providers';
import { AppShell } from '@/components/AppShell';
import { EmbedLayout } from '@/components/EmbedLayout';
import './globals.css';

const inter = Inter({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-inter',
});

/** Browser tab title and description for the console. */
export const metadata: Metadata = {
  title: 'ADO2GitHub Migration Console',
  description: 'Enterprise Azure DevOps to GitHub migration dashboard',
};

/** Viewport scaling and theme colour for the console. */
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 5,
  themeColor: '#050505',
};

/** Root layout wrapping every route in the embed shell, query providers, and app chrome. */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body className={inter.className}>
        <EmbedLayout>
          <Providers>
            <AppShell>{children}</AppShell>
          </Providers>
        </EmbedLayout>
      </body>
    </html>
  );
}
