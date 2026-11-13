// Switching program on a workbench page used to refetch the workbench under the new program.
import { renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { usePinnedProgramId } from './pinned-program';

type Props = { id?: string, program?: string };

function render(initial: Props) {
  return renderHook(({ id, program }: Props) => usePinnedProgramId(id, program), { initialProps: initial });
}

describe('usePinnedProgramId', () => {
  it('keeps the program the page was opened under when the program changes', () => {
    const hook = render({ id: 'pp-1', program: 'program-a' });
    hook.rerender({ id: 'pp-1', program: 'program-b' });
    expect(hook.result.current).toBe('program-a');
  });

  it('takes the current program when the page moves to another provisioned product', () => {
    const hook = render({ id: 'pp-1', program: 'program-a' });
    hook.rerender({ id: 'pp-1', program: 'program-b' });
    hook.rerender({ id: 'pp-2', program: 'program-b' });
    expect(hook.result.current).toBe('program-b');
    hook.rerender({ id: 'pp-2', program: 'program-c' });
    expect(hook.result.current).toBe('program-b');
  });

  it('waits for a program when the page opens before one is selected', () => {
    const hook = render({ id: 'pp-1', program: undefined });
    expect(hook.result.current).toBeUndefined();
    hook.rerender({ id: 'pp-1', program: 'program-a' });
    expect(hook.result.current).toBe('program-a');
    hook.rerender({ id: 'pp-1', program: 'program-b' });
    expect(hook.result.current).toBe('program-a');
  });
});
