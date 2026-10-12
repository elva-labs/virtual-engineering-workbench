/* eslint-disable @typescript-eslint/no-magic-numbers, @stylistic/max-len */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ProjectWorkbenchLifecycleResponse } from '../../../../services/API/project-workbench-lifecycle-api';
import { ProjectShutdownPolicy } from './project-shutdown-policy';
import { inheritedPolicy } from './project-shutdown-policy.logic';

function lifecycle(overrides: Partial<ProjectWorkbenchLifecycleResponse> = {}): ProjectWorkbenchLifecycleResponse {
  return {
    projectId: 'project-1',
    settings: null,
    platformDefaults: {
      idleStopMinutes: 60,
      nightlyStop: false,
      nightlyStopTime: '21:00',
      timezone: 'Europe/Stockholm',
      weekendStop: true,
    },
    deployment: { idleStopEnabled: true, nightlyStopScheduleEnabled: true },
    canEdit: true,
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function input(container: HTMLElement, name: string) {
  return container.querySelector(`[data-test="project-shutdown-${name}"] input`) as HTMLInputElement;
}

function radio(container: HTMLElement, name: string, value: string) {
  const group = container.querySelector(`[data-test="project-shutdown-${name}"]`)!;
  const radios = [...group.querySelectorAll('input')];
  const index = name === 'idle-mode' ? value === 'inherit' ? 0 : 1 : ['inherit', 'true', 'false'].indexOf(value);
  fireEvent.click(radios[index]);
}

describe('ProjectShutdownPolicy', () => {
  it('shows inherited actual defaults, sources, schedule and default owner restrictions', async() => {
    const { container } = render(<ProjectShutdownPolicy projectId="project-1" load={vi.fn().mockResolvedValue(lifecycle())} />);
    await screen.findByText('Effective project defaults');
    expect(screen.getByText('Inherit platform (60 minutes)')).toBeTruthy();
    expect(screen.getByText('60 minutes (platform default)')).toBeTruthy();
    expect(screen.getByText('Off (platform default)')).toBeTruthy();
    expect(screen.getByText('On (platform default)')).toBeTruthy();
    expect(screen.getByText('Shutdown schedule: 21:00 (Europe/Stockholm).')).toBeTruthy();
    expect(input(container, 'min')).toHaveValue(30);
    expect(input(container, 'max')).toHaveValue(480);
    expect(input(container, 'min')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Save policy' })).toBeDisabled();
  });

  it('saves explicit timeout, tri-state stop rules and owner bounds without dropping null fields', async() => {
    const save = vi.fn().mockImplementation((projectId, settings) => Promise.resolve(lifecycle({ projectId, settings })));
    const { container } = render(<ProjectShutdownPolicy projectId="project-1" load={vi.fn().mockResolvedValue(lifecycle())} save={save} />);
    await screen.findByText('Effective project defaults');
    radio(container, 'idle-mode', 'custom');
    fireEvent.change(input(container, 'idle'), { target: { value: '120' } });
    radio(container, 'nightly', 'true');
    fireEvent.click(input(container, 'allow-nightly'));
    fireEvent.click(input(container, 'allow-idle'));
    fireEvent.change(input(container, 'min'), { target: { value: '10' } });
    fireEvent.change(input(container, 'max'), { target: { value: '1440' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save policy' }));
    await waitFor(() => expect(save).toHaveBeenCalledWith('project-1', {
      ...inheritedPolicy,
      idleStopMinutes: 120,
      nightlyStop: true,
      allowUserDisableNightlyStop: true,
      allowUserIdleTimeout: true,
      userIdleTimeoutMinMinutes: 10,
      userIdleTimeoutMaxMinutes: 1440,
    }));
    expect(await screen.findByText('Shutdown policy saved.')).toBeTruthy();
    expect(screen.getByText('120 minutes (program policy)')).toBeTruthy();
  });

  it('lets explicit nightly/weekend and idle values return to inheritance', async() => {
    const settings = { ...inheritedPolicy, idleStopMinutes: 90, nightlyStop: true, weekendStop: false };
    const save = vi.fn().mockImplementation((projectId, chosen) => Promise.resolve(lifecycle({ projectId, settings: chosen })));
    const { container } = render(<ProjectShutdownPolicy projectId="project-1" load={vi.fn().mockResolvedValue(lifecycle({ settings }))} save={save} />);
    await screen.findByText('Effective project defaults');
    radio(container, 'idle-mode', 'inherit');
    radio(container, 'nightly', 'inherit');
    radio(container, 'weekend', 'inherit');
    fireEvent.click(screen.getByRole('button', { name: 'Save policy' }));
    await waitFor(() => expect(save).toHaveBeenCalledWith('project-1', inheritedPolicy));
  });

  it('retains stored stop rules, permissions and bounds while toggling always-on or owner permissions', async() => {
    const settings = {
      ...inheritedPolicy, idleStopMinutes: 90, nightlyStop: true, weekendStop: false,
      allowUserDisableNightlyStop: true, allowUserIdleTimeout: true,
      userIdleTimeoutMinMinutes: 20, userIdleTimeoutMaxMinutes: 200,
    };
    const save = vi.fn().mockImplementation((projectId, chosen) => Promise.resolve(lifecycle({ projectId, settings: chosen })));
    const { container } = render(<ProjectShutdownPolicy projectId="project-1" load={vi.fn().mockResolvedValue(lifecycle({ settings }))} save={save} />);
    await screen.findByText('Effective project defaults');
    fireEvent.click(input(container, 'allow-idle'));
    expect(input(container, 'min')).toBeDisabled();
    expect(input(container, 'min')).toHaveValue(20);
    fireEvent.click(input(container, 'allow-idle'));
    fireEvent.click(input(container, 'always-on'));
    expect(screen.getByText(/Always-on takes precedence/u)).toBeTruthy();
    expect(input(container, 'idle')).toHaveValue(90);
    expect(input(container, 'idle')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Save policy' }));
    await waitFor(() => expect(save).toHaveBeenCalledWith('project-1', { ...settings, alwaysOn: true }));
    await screen.findByText('Shutdown policy saved.');
    fireEvent.click(input(container, 'always-on'));
    expect(input(container, 'min')).toHaveValue(20);
    expect(input(container, 'min')).not.toBeDisabled();
    expect(screen.getByText('90 minutes (program policy)')).toBeTruthy();
  });

  it.each(['9', '1441', '10.5', ''])('rejects an invalid timeout %j before saving', async(value) => {
    const save = vi.fn();
    const { container } = render(<ProjectShutdownPolicy projectId="project-1" load={vi.fn().mockResolvedValue(lifecycle())} save={save} />);
    await screen.findByText('Effective project defaults');
    radio(container, 'idle-mode', 'custom');
    fireEvent.change(input(container, 'idle'), { target: { value } });
    expect(screen.getByText('Enter a whole number from 10 to 1440 minutes.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Save policy' })).toBeDisabled();
    expect(save).not.toHaveBeenCalled();
  });

  it('rejects reversed owner bounds and invalid owner bounds', async() => {
    const { container } = render(<ProjectShutdownPolicy projectId="project-1" load={vi.fn().mockResolvedValue(lifecycle({ settings: { ...inheritedPolicy, allowUserIdleTimeout: true } }))} />);
    await screen.findByText('Effective project defaults');
    fireEvent.change(input(container, 'min'), { target: { value: '600' } });
    expect(screen.getByText('Maximum must be at least the minimum timeout.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Save policy' })).toBeDisabled();
    fireEvent.change(input(container, 'min'), { target: { value: '9' } });
    expect(screen.getByText('Enter a whole number from 10 to 1440 minutes.')).toBeTruthy();
  });

  it('removes the explicit policy on reset and shows inherited defaults and restrictions', async() => {
    const reset = vi.fn().mockResolvedValue(lifecycle());
    const { container } = render(<ProjectShutdownPolicy projectId="project-1" load={vi.fn().mockResolvedValue(lifecycle({ settings: { ...inheritedPolicy, alwaysOn: true, allowUserIdleTimeout: true } }))} reset={reset} />);
    await screen.findByText('Effective project defaults');
    fireEvent.click(screen.getByRole('button', { name: 'Reset to platform defaults' }));
    await waitFor(() => expect(reset).toHaveBeenCalledWith('project-1'));
    expect(await screen.findByText('Platform defaults restored.')).toBeTruthy();
    expect(input(container, 'always-on')).not.toBeChecked();
    expect(input(container, 'allow-idle')).not.toBeChecked();
    expect(screen.getByText('60 minutes (platform default)')).toBeTruthy();
  });

  it('discards a local draft without a server mutation', async() => {
    const save = vi.fn();
    const { container } = render(<ProjectShutdownPolicy projectId="project-1" load={vi.fn().mockResolvedValue(lifecycle())} save={save} />);
    await screen.findByText('Effective project defaults');
    fireEvent.click(input(container, 'always-on'));
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    expect(input(container, 'always-on')).not.toBeChecked();
    expect(screen.getByText('Effective project defaults')).toBeTruthy();
    expect(save).not.toHaveBeenCalled();
  });

  it.each([
    { canEdit: false },
    { canEdit: false, managedBy: 'terraform', managedSource: 'org/config/programs/example' },
  ])('uses authoritative read-only permission and management context %j', async(overrides) => {
    const save = vi.fn();
    const { container } = render(<ProjectShutdownPolicy projectId="project-1" load={vi.fn().mockResolvedValue(lifecycle(overrides))} save={save} />);
    await screen.findByText('Effective project defaults');
    expect([...container.querySelectorAll('input')].every(control => control.disabled)).toBe(true);
    expect(screen.queryByRole('button', { name: 'Save policy' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Reset to platform defaults' })).toBeNull();
    expect(screen.getByText(overrides.managedBy ? /managed by terraform \(org\/config\/programs\/example\)/u : /Only administrators and owners/u)).toBeTruthy();
    expect(save).not.toHaveBeenCalled();
  });

  it('shows deployment-disabled monitoring without an idle opt-in switch', async() => {
    render(<ProjectShutdownPolicy projectId="project-1" load={vi.fn().mockResolvedValue(lifecycle({
      settings: { ...inheritedPolicy, idleStopMinutes: 90, nightlyStop: true },
      deployment: { idleStopEnabled: false, nightlyStopScheduleEnabled: false },
    }))} />);
    await screen.findByText('Effective project defaults');
    expect(screen.getByText(/Central idle monitoring is disabled/u)).toBeTruthy();
    expect(screen.getByText('Off (deployment monitoring disabled)')).toBeTruthy();
    expect(screen.getByText('Off (deployment schedule disabled)')).toBeTruthy();
    expect(screen.getByText('On (platform default)')).toBeTruthy();
  });

  it('retries a failed load and preserves drafts after a failed save or reset', async() => {
    const load = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(lifecycle());
    const save = vi.fn().mockRejectedValue(new Error('save refused'));
    const reset = vi.fn().mockRejectedValue(new Error('reset refused'));
    const { container } = render(<ProjectShutdownPolicy projectId="project-1" load={load} save={save} reset={reset} />);
    expect(await screen.findByText('Unable to load shutdown policy: offline')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await screen.findByText('Effective project defaults');
    fireEvent.click(input(container, 'always-on'));
    fireEvent.click(screen.getByRole('button', { name: 'Save policy' }));
    expect(await screen.findByText('Unable to save shutdown policy: save refused')).toBeTruthy();
    expect(input(container, 'always-on')).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Reset to platform defaults' }));
    expect(await screen.findByText('Unable to reset shutdown policy: reset refused')).toBeTruthy();
    expect(input(container, 'always-on')).toBeChecked();
  });

  it('does not show the old program or adopt its late load when switching projects', async() => {
    const first = deferred<ProjectWorkbenchLifecycleResponse>();
    const second = deferred<ProjectWorkbenchLifecycleResponse>();
    const load = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { rerender } = render(<ProjectShutdownPolicy projectId="project-1" load={load} />);
    rerender(<ProjectShutdownPolicy projectId="project-2" load={load} />);
    await act(() => { first.resolve(lifecycle({ settings: { ...inheritedPolicy, alwaysOn: true } })); });
    expect(screen.queryByText('Effective project defaults')).toBeNull();
    await act(() => { second.resolve(lifecycle({ projectId: 'project-2', platformDefaults: { ...lifecycle().platformDefaults, idleStopMinutes: 120 } })); });
    expect(screen.getByText('120 minutes (platform default)')).toBeTruthy();
    expect(screen.queryByText(/Always-on takes precedence/u)).toBeNull();
  });

  it('hides saved values immediately when switching from a loaded program to a loading program', async() => {
    const pending = deferred<ProjectWorkbenchLifecycleResponse>();
    const load = vi.fn().mockResolvedValueOnce(lifecycle({ settings: { ...inheritedPolicy, idleStopMinutes: 90 } }))
      .mockReturnValueOnce(pending.promise);
    const { rerender } = render(<ProjectShutdownPolicy projectId="project-1" load={load} />);
    await screen.findByText('90 minutes (program policy)');
    rerender(<ProjectShutdownPolicy projectId="project-2" load={load} />);
    expect(screen.queryByText('90 minutes (program policy)')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Save policy' })).toBeNull();
    await act(() => { pending.resolve(lifecycle({ projectId: 'project-2' })); });
    expect(screen.getByText('60 minutes (platform default)')).toBeTruthy();
  });

  it('locks mutations and fields while saving', async() => {
    const pending = deferred<ProjectWorkbenchLifecycleResponse>();
    const save = vi.fn().mockReturnValue(pending.promise);
    const reset = vi.fn();
    const { container } = render(<ProjectShutdownPolicy projectId="project-1"
      load={vi.fn().mockResolvedValue(lifecycle())} save={save} reset={reset} />);
    await screen.findByText('Effective project defaults');
    fireEvent.click(input(container, 'always-on'));
    fireEvent.click(screen.getByRole('button', { name: 'Save policy' }));
    expect(input(container, 'always-on')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Reset to platform defaults' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Save policy' }));
    expect(save).toHaveBeenCalledTimes(1);
    expect(reset).not.toHaveBeenCalled();
    await act(() => { pending.resolve(lifecycle({ settings: { ...inheritedPolicy, alwaysOn: true } })); });
    expect(input(container, 'always-on')).not.toBeDisabled();
  });

  it.each(['resolve', 'reject'] as const)('ignores a late save %s after switching projects', async(completion) => {
    const pending = deferred<ProjectWorkbenchLifecycleResponse>();
    const load = vi.fn().mockImplementation((projectId) => Promise.resolve(lifecycle({ projectId })));
    const save = vi.fn().mockReturnValue(pending.promise);
    const { container, rerender } = render(<ProjectShutdownPolicy projectId="project-1" load={load} save={save} />);
    await screen.findByText('Effective project defaults');
    fireEvent.click(input(container, 'always-on'));
    fireEvent.click(screen.getByRole('button', { name: 'Save policy' }));
    await waitFor(() => expect(save).toHaveBeenCalled());
    rerender(<ProjectShutdownPolicy projectId="project-2" load={load} save={save} />);
    await screen.findByText('Effective project defaults');
    await act(() => {
      if (completion === 'resolve') {
        pending.resolve(lifecycle({ settings: { ...inheritedPolicy, alwaysOn: true } }));
      } else {
        pending.reject(new Error('old save failed'));
      }
    });
    expect(input(container, 'always-on')).not.toBeChecked();
    expect(screen.queryByText('Shutdown policy saved.')).toBeNull();
    expect(screen.queryByText(/old save failed/u)).toBeNull();
  });
});
