// A page loaded before a deployment still refers to the previous build's chunks, which the
// deployment removes (`aws s3 sync --delete` in deploy.sh). Its import of main.tsx then fails
// with "Failed to fetch dynamically imported module". Reloading fetches the current index.html
// (served with Cache-Control: no-cache, app-elb.alb-target.ts) and with it the current chunks.
// At most one reload a minute, so a chunk that is really missing shows the error instead of a loop.

export const RELOAD_KEY = 'vew-stale-build-reload';
const RELOAD_WINDOW_MS = 60_000;

/** True when it reloaded the page; false when it already did so within the last minute. */
export function reloadOnceForStaleBuild(
  storage: Pick<Storage, 'getItem' | 'setItem'> | undefined = sessionStorageOrUndefined(),
  reload: () => void = () => window.location.reload(),
  now: number = Date.now(),
): boolean {
  // Without storage (blocked in a private window or by site settings) there is no guard: no reload.
  if (storage === undefined) {
    return false;
  }
  try {
    const last = Number(storage.getItem(RELOAD_KEY));
    if (now - last < RELOAD_WINDOW_MS) {
      return false;
    }
    storage.setItem(RELOAD_KEY, String(now));
  } catch {
    return false;
  }
  reload();
  return true;
}

function sessionStorageOrUndefined(): Storage | undefined {
  try {
    return window.sessionStorage;
  } catch {
    return undefined;
  }
}
