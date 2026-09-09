import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

const ACCEL =
  process.env.ACCELERATOR_INTERNAL_URL ||
  process.env.NEXT_PUBLIC_ACCELERATOR_URL ||
  'http://localhost:8080';

/**
 * Send unauthenticated page requests to the login screen.
 *
 * Public paths and requests that already carry a session cookie pass straight through.
 * Otherwise the accelerator's bootstrap status decides whether the login link asks for
 * first-run bootstrap, and the original path rides along as `returnUrl`. When the API is
 * unreachable the request is allowed through and the client-side AuthGate redirects.
 */
export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (
    pathname === '/login' ||
    pathname.startsWith('/onboarding') ||
    pathname.startsWith('/_next') ||
    pathname === '/favicon.ico'
  ) {
    return NextResponse.next();
  }

  if (request.cookies.get('ado2gh_session')?.value) {
    return NextResponse.next();
  }

  try {
    const response = await fetch(`${ACCEL}/v1/auth/bootstrap-status`, { cache: 'no-store' });
    if (!response.ok) {
      return NextResponse.next();
    }
    const status = (await response.json()) as { needs_bootstrap?: boolean };
    const login = new URL(
      status.needs_bootstrap ? '/login?bootstrap=1' : '/login',
      request.url,
    );
    login.searchParams.set('returnUrl', pathname);
    return NextResponse.redirect(login);
  } catch {
    // AuthGate handles client-side redirect when middleware cannot reach the API.
    return NextResponse.next();
  }
}

/** Run the middleware on every route except Next.js static assets and the favicon. */
export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
