/**
 * Stand-in for `next/navigation` used by the GAP-025 render tests.
 *
 * The navigation hooks throw outside a mounted Next.js router, so every test that renders
 * a client component using them declares
 * `vi.mock('next/navigation', () => import('@/__tests__/nextNavigationStub'))`. The mocked
 * namespace and a direct import of this module are the same instance, so a test can set
 * `navState.pathname` before rendering and read `redirect.mock.calls` afterwards.
 */
import { vi } from 'vitest';

/** Mutable route state the stubbed hooks read; set it before rendering. */
export const navState = {
  pathname: '/',
  search: '',
  params: {} as Record<string, string>,
};

/** Reset the stub between tests: root path, no query string, no route params, no calls. */
export function resetNavState(): void {
  navState.pathname = '/';
  navState.search = '';
  navState.params = {};
  redirect.mockClear();
  notFound.mockClear();
  routerPush.mockClear();
  routerReplace.mockClear();
}

/** Spy standing in for `redirect()`; a render-time redirect records its target here. */
export const redirect = vi.fn();

/** Spy standing in for `notFound()`. */
export const notFound = vi.fn();

/** Spy standing in for `router.push()`. */
export const routerPush = vi.fn();

/** Spy standing in for `router.replace()`. */
export const routerReplace = vi.fn();

/** Current path, from `navState.pathname`. */
export function usePathname(): string {
  return navState.pathname;
}

/** Current query string, from `navState.search`. */
export function useSearchParams(): URLSearchParams {
  return new URLSearchParams(navState.search);
}

/** Current dynamic route params, from `navState.params`. */
export function useParams(): Record<string, string> {
  return navState.params;
}

/** Router object whose navigation methods are the spies exported above. */
export function useRouter() {
  return {
    push: routerPush,
    replace: routerReplace,
    refresh: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    prefetch: vi.fn(),
  };
}
