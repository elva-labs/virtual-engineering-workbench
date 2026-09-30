import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { WorkbenchSettingsPanel } from './workbench-settings';
import { i18nWorkbenchSettings } from './translations';

const MIN = 10;
const MAX = 240;
const PROGRAM_MIN = 30;
const PROGRAM_MAX = 480;
const CHOSEN = 15;
const TOO_LONG = 500;
const BOTH_FIELDS = 2;
const PLATFORM_IDLE = 60;

function lifecycle(overrides: Record<string, unknown> = {}) {
  return {
    provisionedProductId: 'pp-1',
    projectId: 'proj-1',
    canEdit: true,
    nightlyStopTime: '21:00',
    nightlyStopTimezone: 'Europe/Stockholm',
    userSettings: { nightlyStopDisabled: false, idleTimeoutMinutes: null },
    permissions: {
      mayDisableNightlyStop: true,
      maySetIdleTimeout: true,
      idleTimeoutMinMinutes: MIN,
      idleTimeoutMaxMinutes: MAX,
    },
    effective: {
      alwaysOn: false,
      idleStopEnabled: true,
      idleStopMinutes: PLATFORM_IDLE,
      nightlyStop: true,
      weekendStop: true,
      sources: { idleStopMinutes: 'platform', nightlyStop: 'platform', weekendStop: 'platform' },
      ignoredUserSettings: [],
    },
    ...overrides,
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function renderPanel(load: any, save: any = vi.fn()) {
  return render(
    <WorkbenchSettingsPanel projectId="proj-1" provisionedProductId="pp-1" load={load} save={save} />
  );
}

describe('WorkbenchSettingsPanel', () => {
  it('shows the effective values and where they come from', async () => {
    renderPanel(vi.fn().mockResolvedValue(lifecycle()));

    expect(await screen.findByText(i18nWorkbenchSettings.header)).toBeTruthy();
    expect(screen.getByText('60 minutes (platform default)')).toBeTruthy();
    const description = i18nWorkbenchSettings.nightlyStopDescription('21:00', 'Europe/Stockholm');
    expect(screen.getByText(description)).toBeTruthy();
  });

  it('saves the owner\'s choice within the bounds', async () => {
    const saved = lifecycle({
      userSettings: { nightlyStopDisabled: false, idleTimeoutMinutes: CHOSEN },
      effective: { ...lifecycle().effective, idleStopMinutes: CHOSEN, sources: { idleStopMinutes: 'user' } },
    });
    const save = vi.fn().mockResolvedValue(saved);
    const { container } = renderPanel(vi.fn().mockResolvedValue(lifecycle()), save);
    await screen.findByText(i18nWorkbenchSettings.header);

    const input = container.querySelector('[data-test="workbench-settings-idle"] input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: String(CHOSEN) } });
    fireEvent.click(screen.getByText(i18nWorkbenchSettings.save));

    await waitFor(() => expect(save).toHaveBeenCalledWith('proj-1', 'pp-1', {
      nightlyStopDisabled: false,
      idleTimeoutMinutes: CHOSEN,
    }));
    expect(await screen.findByText('15 minutes (owner\'s choice)')).toBeTruthy();
  });

  it('refuses a timeout outside the program\'s bounds before saving', async () => {
    const save = vi.fn();
    const { container } = renderPanel(vi.fn().mockResolvedValue(lifecycle()), save);
    await screen.findByText(i18nWorkbenchSettings.header);

    const input = container.querySelector('[data-test="workbench-settings-idle"] input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: String(TOO_LONG) } });

    expect(await screen.findByText(i18nWorkbenchSettings.idleTimeoutOutOfBounds(MIN, MAX))).toBeTruthy();
    expect(save).not.toHaveBeenCalled();
  });

  it('disables what the program does not allow', async () => {
    const { container } = renderPanel(vi.fn().mockResolvedValue(lifecycle({
      permissions: {
        mayDisableNightlyStop: false,
        maySetIdleTimeout: false,
        idleTimeoutMinMinutes: PROGRAM_MIN,
        idleTimeoutMaxMinutes: PROGRAM_MAX,
      },
    })));
    await screen.findByText(i18nWorkbenchSettings.header);

    const input = container.querySelector('[data-test="workbench-settings-idle"] input') as HTMLInputElement;
    expect(input.disabled).toBe(true);
    expect(screen.getAllByText(i18nWorkbenchSettings.notAllowed).length).toBe(BOTH_FIELDS);
    expect(screen.queryByText(i18nWorkbenchSettings.save)).toBeNull();
  });

  it('tells program-wide always-on programs apart', async () => {
    renderPanel(vi.fn().mockResolvedValue(lifecycle({
      effective: {
        ...lifecycle().effective,
        alwaysOn: true,
        idleStopEnabled: false,
        nightlyStop: false,
        weekendStop: false,
      },
    })));

    expect(await screen.findByText(i18nWorkbenchSettings.alwaysOn)).toBeTruthy();
  });

  it('stays hidden when the settings cannot be loaded', async () => {
    const load = vi.fn().mockRejectedValue(new Error('403'));

    const { container } = renderPanel(load);

    await waitFor(() => expect(load).toHaveBeenCalled());
    expect(container.innerHTML).toBe('');
  });
});
