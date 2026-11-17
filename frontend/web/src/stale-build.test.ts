/* eslint-disable @typescript-eslint/no-magic-numbers */
import { RELOAD_KEY, reloadOnceForStaleBuild } from './stale-build';

function memoryStorage(initial: Record<string, string> = {}) {
  const items = { ...initial };
  return {
    items,
    getItem: (key: string) => items[key] ?? null,
    setItem: (key: string, value: string) => {
      items[key] = value;
    },
  };
}

describe('reloadOnceForStaleBuild', () => {
  it('reloads once and remembers when', () => {
    const storage = memoryStorage();
    const reload = vi.fn();

    expect(reloadOnceForStaleBuild(storage, reload, 1_000_000)).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(storage.items[RELOAD_KEY]).toBe('1000000');
  });

  it('does not reload again within a minute, so a missing chunk cannot loop', () => {
    const storage = memoryStorage({ [RELOAD_KEY]: '1000000' });
    const reload = vi.fn();

    expect(reloadOnceForStaleBuild(storage, reload, 1_030_000)).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });

  it('reloads again after the next deployment', () => {
    const storage = memoryStorage({ [RELOAD_KEY]: '1000000' });
    const reload = vi.fn();

    expect(reloadOnceForStaleBuild(storage, reload, 1_000_000 + 61_000)).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('does not reload when storage is blocked, as there is no guard', () => {
    const reload = vi.fn();
    const throwing = {
      getItem: () => {
        throw new Error('blocked');
      },
      setItem: () => undefined,
    };

    expect(reloadOnceForStaleBuild(throwing, reload)).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });
});
