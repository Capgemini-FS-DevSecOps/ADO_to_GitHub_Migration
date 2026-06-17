import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

const REQUIRE_AUTH = process.env.NEXT_PUBLIC_REQUIRE_AUTH === 'true';

export function middleware(request: NextRequest) {
  if (!REQUIRE_AUTH) {
    return NextResponse.next();
  }
  const { pathname } = request.nextUrl;
  if (pathname === '/login' || pathname.startsWith('/onboarding') || pathname.startsWith('/_next') || pathname === '/favicon.ico') {
    return NextResponse.next();
  }
  const session = request.cookies.get('ado2gh_session');
  if (!session?.value) {
    const login = new URL('/login', request.url);
    login.searchParams.set('returnUrl', pathname);
    return NextResponse.redirect(login);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
