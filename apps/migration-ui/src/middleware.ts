import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

const ACCEL =
  process.env.ACCELERATOR_INTERNAL_URL ||
  process.env.NEXT_PUBLIC_ACCELERATOR_URL ||
  'http://localhost:8080';

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

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
