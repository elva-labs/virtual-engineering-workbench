export const i18nWorkbenchSettings = {
  header: 'Settings',
  description: 'When your workbench stops by itself. It never starts by itself.',
  nightlyStopLabel: 'Disable nightly shutdown',
  nightlyStopDescription: (time: string, timezone: string) =>
    `Running workbenches are shut down every night at ${time} (${timezone}).`,
  idleTimeoutLabel: 'Inactivity timeout (minutes)',
  idleTimeoutDescription: (min: number, max: number) =>
    'The workbench stops after this many minutes without a DCV connection, session or load. ' +
    `Allowed: ${min}-${max} minutes. Empty uses the program's default.`,
  idleTimeoutOutOfBounds: (min: number, max: number) => `Enter a value between ${min} and ${max}.`,
  notAllowed: 'Your program does not allow changing this.',
  alwaysOn: 'Your program keeps its workbenches running: no automatic stops.',
  costNotice: 'A workbench that keeps running costs money until it stops. Your program pays for it.',
  save: 'Save',
  saved: 'Settings saved.',
  saveError: 'Could not save the settings',
  effectiveHeader: 'In effect',
  effectiveIdle: 'Stops after being idle',
  effectiveIdleValue: (minutes: number) => `${minutes} minutes`,
  effectiveNightly: 'Nightly shutdown',
  effectiveWeekend: 'Weekend shutdown',
  on: 'On',
  off: 'Off',
  sources: {
    platform: 'platform default',
    program: 'program setting',
    user: 'owner\'s choice',
  } as Record<string, string>,
  ignored: 'Some of the owner\'s choices are no longer allowed by the program and are not applied.',
  readOnly: 'Only the owner of the workbench can change these settings.',
};
