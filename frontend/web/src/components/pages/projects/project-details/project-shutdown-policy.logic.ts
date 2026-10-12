import {
  ProjectWorkbenchLifecycle,
  ProjectWorkbenchLifecycleResponse,
} from '../../../../services/API/project-workbench-lifecycle-api';

const DEFAULT_OWNER_MINIMUM = 30;
const DEFAULT_OWNER_MAXIMUM = 480;
const MINIMUM_IDLE_TIMEOUT = 10;
const MAXIMUM_IDLE_TIMEOUT = 1440;

export const inheritedPolicy: ProjectWorkbenchLifecycle = {
  alwaysOn: false,
  idleStopMinutes: null,
  nightlyStop: null,
  weekendStop: null,
  allowUserDisableNightlyStop: false,
  allowUserIdleTimeout: false,
  userIdleTimeoutMinMinutes: DEFAULT_OWNER_MINIMUM,
  userIdleTimeoutMaxMinutes: DEFAULT_OWNER_MAXIMUM,
};

export function minutesError(value: string): string | undefined {
  const minutes = Number(value);
  return value.trim() === '' || !Number.isInteger(minutes) ||
    minutes < MINIMUM_IDLE_TIMEOUT || minutes > MAXIMUM_IDLE_TIMEOUT
    ? 'Enter a whole number from 10 to 1440 minutes.'
    : undefined;
}

export function projectPolicyDefaults(
  response: ProjectWorkbenchLifecycleResponse,
  settings: ProjectWorkbenchLifecycle,
) {
  const idle = settings.idleStopMinutes ?? response.platformDefaults.idleStopMinutes;
  const nightly = settings.nightlyStop ?? response.platformDefaults.nightlyStop;
  const weekend = settings.weekendStop ?? response.platformDefaults.weekendStop;
  const source = (value: unknown) => value === null ? 'platform default' : 'program policy';
  return {
    idleMinutes: idle,
    idleEnabled: !settings.alwaysOn && response.deployment.idleStopEnabled,
    nightlyEnabled: !settings.alwaysOn && response.deployment.nightlyStopScheduleEnabled && nightly,
    weekendEnabled: !settings.alwaysOn && weekend,
    idleSource: source(settings.idleStopMinutes),
    nightlySource: source(settings.nightlyStop),
    weekendSource: source(settings.weekendStop),
  };
}
