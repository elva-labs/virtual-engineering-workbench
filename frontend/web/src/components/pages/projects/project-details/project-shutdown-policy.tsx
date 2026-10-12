import { useEffect, useRef, useState } from 'react';
import {
  Alert, Box, Button, ColumnLayout, Container, FormField, Header, Input, RadioGroup,
  SpaceBetween, Spinner, Toggle,
} from '@cloudscape-design/components';
import {
  ProjectWorkbenchLifecycle,
  ProjectWorkbenchLifecycleResponse,
  projectWorkbenchLifecycleAPI,
} from '../../../../services/API/project-workbench-lifecycle-api';
import { extractErrorResponseMessage } from '../../../../utils/api-helpers';
import { inheritedPolicy, minutesError, projectPolicyDefaults } from './project-shutdown-policy.logic';

interface ProjectShutdownPolicyProps {
  projectId: string,
  load?: typeof projectWorkbenchLifecycleAPI.get,
  save?: typeof projectWorkbenchLifecycleAPI.update,
  reset?: typeof projectWorkbenchLifecycleAPI.reset,
}

type Message = { type: 'success' | 'error', text: string };
const INITIAL_REQUEST = 0;
const onOff = (value: boolean) => value ? 'On' : 'Off';
const choice = (value: boolean | null) => value === null ? 'inherit' : String(value);
const booleanValue = (value: string): boolean | null => value === 'inherit' ? null : value === 'true';

// Remount on project changes so drafts and in-flight requests never leak between programs.
export function ProjectShutdownPolicy(props: ProjectShutdownPolicyProps) {
  return <ProjectShutdownPolicyForm key={props.projectId} {...props} />;
}

// Form rendering includes independent policy and permission controls.
// eslint-disable-next-line complexity
function ProjectShutdownPolicyForm({
  projectId,
  load = projectWorkbenchLifecycleAPI.get,
  save = projectWorkbenchLifecycleAPI.update,
  reset = projectWorkbenchLifecycleAPI.reset,
}: ProjectShutdownPolicyProps) {
  const [response, setResponse] = useState<ProjectWorkbenchLifecycleResponse>();
  const [policy, setPolicy] = useState<ProjectWorkbenchLifecycle>(inheritedPolicy);
  const [idleChoice, setIdleChoice] = useState('inherit');
  const [idleMinutes, setIdleMinutes] = useState('');
  const [minimum, setMinimum] = useState(String(inheritedPolicy.userIdleTimeoutMinMinutes));
  const [maximum, setMaximum] = useState(String(inheritedPolicy.userIdleTimeoutMaxMinutes));
  const [loadError, setLoadError] = useState<string>();
  const [message, setMessage] = useState<Message>();
  const [busy, setBusy] = useState(false);
  const request = useRef(INITIAL_REQUEST);

  function adopt(result: ProjectWorkbenchLifecycleResponse) {
    if (result.projectId !== projectId) {
      throw new Error('The shutdown policy response belongs to a different program. Please retry.');
    }
    const settings = result.settings ?? inheritedPolicy;
    setResponse(result);
    setPolicy(settings);
    setIdleChoice(settings.idleStopMinutes === null ? 'inherit' : 'custom');
    setIdleMinutes(String(settings.idleStopMinutes ?? result.platformDefaults.idleStopMinutes));
    setMinimum(String(settings.userIdleTimeoutMinMinutes));
    setMaximum(String(settings.userIdleTimeoutMaxMinutes));
  }

  async function errorText(error: unknown) {
    try {
      return await extractErrorResponseMessage(error) || 'Please try again.';
    } catch {
      return error instanceof Error ? error.message : 'Please try again.';
    }
  }

  async function reload() {
    const current = ++request.current;
    setLoadError(undefined);
    setResponse(undefined);
    setMessage(undefined);
    try {
      const result = await load(projectId);
      if (current === request.current) {
        adopt(result);
      }
    } catch (error) {
      const text = await errorText(error);
      if (current === request.current) {
        setLoadError(text);
      }
    }
  }

  // Loading initializes the draft; the ref is a request counter, not a DOM ref.
  /* eslint-disable react-hooks/set-state-in-effect, react-hooks/exhaustive-deps */
  useEffect(() => {
    reload();
    return () => { request.current++; };
  }, [projectId, load]);
  /* eslint-enable react-hooks/set-state-in-effect, react-hooks/exhaustive-deps */

  const idleError = idleChoice === 'custom' ? minutesError(idleMinutes) : undefined;
  const minError = minutesError(minimum);
  const maxError = minutesError(maximum) ||
    (!minError && Number(minimum) > Number(maximum)
      ? 'Maximum must be at least the minimum timeout.' : undefined);
  const invalid = !!(idleError || minError || maxError);
  const draft: ProjectWorkbenchLifecycle = {
    ...policy,
    idleStopMinutes: idleChoice === 'inherit' ? null : Number(idleMinutes),
    userIdleTimeoutMinMinutes: Number(minimum),
    userIdleTimeoutMaxMinutes: Number(maximum),
  };
  const dirty = response && JSON.stringify(draft) !== JSON.stringify(response.settings ?? inheritedPolicy);

  // eslint-disable-next-line complexity
  async function mutate(resetToPlatform: boolean) {
    if (!response?.canEdit || busy || !resetToPlatform && invalid) {
      return;
    }
    const current = ++request.current;
    setBusy(true);
    setMessage(undefined);
    try {
      const result = resetToPlatform ? await reset(projectId) : await save(projectId, draft);
      if (current === request.current) {
        adopt(result);
        setMessage({
          type: 'success',
          text: resetToPlatform ? 'Platform defaults restored.' : 'Shutdown policy saved.',
        });
      }
    } catch (error) {
      const text = await errorText(error);
      if (current === request.current) {
        setMessage({
          type: 'error',
          text: `Unable to ${resetToPlatform ? 'reset' : 'save'} shutdown policy: ${text}`,
        });
      }
    } finally {
      if (current === request.current) {
        setBusy(false);
      }
    }
  }

  const header = <Header variant="h2" description={
    'Control automated shutdowns for workbenches across this program\'s AWS accounts. ' +
    'Workbenches never start automatically.'
  }>
    Shutdown policy
  </Header>;

  if (!response) {
    return <Container header={header}>
      {loadError ? <Alert type="error" action={<Button onClick={reload}>Retry</Button>}>
        Unable to load shutdown policy: {loadError}
      </Alert> : <Spinner />}
    </Container>;
  }

  const { nightlyStopTime, timezone } = response.platformDefaults;
  const disabled = !response.canEdit || busy;
  const stopDisabled = disabled || policy.alwaysOn;
  const effective = projectPolicyDefaults(response, draft);
  const options = (platform: boolean) => [
    { value: 'inherit', label: `Inherit platform (${onOff(platform)})`, disabled: stopDisabled },
    { value: 'true', label: 'On', disabled: stopDisabled },
    { value: 'false', label: 'Off', disabled: stopDisabled },
  ];
  const manager = response.managedBy + (response.managedSource ? ` (${response.managedSource})` : '');
  const idleSummary = policy.alwaysOn ? 'Off (always-on mode)' :
    !response.deployment.idleStopEnabled ? 'Off (deployment monitoring disabled)' :
      idleError ? 'Enter a valid timeout' : `${effective.idleMinutes} minutes (${effective.idleSource})`;
  const nightlySource = policy.alwaysOn ? 'always-on mode' :
    !response.deployment.nightlyStopScheduleEnabled
      ? 'deployment schedule disabled' : effective.nightlySource;

  const weekendSource = policy.alwaysOn ? 'always-on mode' : effective.weekendSource;

  return <Container header={header}>
    <SpaceBetween size="l">
      {message && <Alert type={message.type}>{message.text}</Alert>}
      {!response.canEdit && <Alert type="info">
        {response.managedBy
          ? `This program is managed by ${manager}. Update its shutdown policy at that source.`
          : 'Only administrators and owners of this program can edit its shutdown policy.'}
      </Alert>}
      {!response.deployment.idleStopEnabled && <Alert type="info">
        Central idle monitoring is disabled for this deployment. The timeout below is a policy preference
        and does not enable idle shutdowns.
      </Alert>}
      {!response.deployment.nightlyStopScheduleEnabled && <Alert type="info">
        The deployment's nightly shutdown schedule is disabled. The nightly policy preference will apply
        when the schedule is enabled.
      </Alert>}
      {policy.alwaysOn && <Alert type="info">
        Always-on takes precedence over idle, nightly and weekend shutdowns, including owner overrides.
        Other policy values are retained and apply again when always-on is switched off.
      </Alert>}
      <FormField label="Always-on mode" description={
        'Keep all workbenches in this program running until someone stops them manually. ' +
        'This can increase costs.'
      }>
        <Toggle data-test="project-shutdown-always-on" checked={policy.alwaysOn} disabled={disabled}
          onChange={({ detail }) => setPolicy({ ...policy, alwaysOn: detail.checked })}>Always on</Toggle>
      </FormField>
      <FormField label="Default idle timeout"
        description="Stop idle workbenches after this many minutes." errorText={idleError}>
        <SpaceBetween size="s">
          <RadioGroup data-test="project-shutdown-idle-mode" value={idleChoice}
            items={[
              {
                value: 'inherit',
                label: `Inherit platform (${response.platformDefaults.idleStopMinutes} minutes)`,
                disabled: stopDisabled,
              },
              { value: 'custom', label: 'Use program timeout', disabled: stopDisabled },
            ]}
            onChange={({ detail }) => setIdleChoice(detail.value)} />
          <Input data-test="project-shutdown-idle" ariaLabel="Program idle timeout in minutes"
            type="number" inputMode="numeric" value={idleMinutes}
            disabled={stopDisabled || idleChoice === 'inherit'}
            onChange={({ detail }) => setIdleMinutes(detail.value)} />
        </SpaceBetween>
      </FormField>
      <ColumnLayout columns={2}>
        <FormField label="Nightly shutdown" description={
          `Shutdown schedule: ${nightlyStopTime} (${timezone}).`
        }>
          <RadioGroup data-test="project-shutdown-nightly" value={choice(policy.nightlyStop)}
            items={options(response.platformDefaults.nightlyStop)}
            onChange={({ detail }) => setPolicy({ ...policy, nightlyStop: booleanValue(detail.value) })} />
        </FormField>
        <FormField label="Weekend shutdown"
          description="Apply the platform's weekend stop rule.">
          <RadioGroup data-test="project-shutdown-weekend" value={choice(policy.weekendStop)}
            items={options(response.platformDefaults.weekendStop)}
            onChange={({ detail }) => setPolicy({ ...policy, weekendStop: booleanValue(detail.value) })} />
        </FormField>
      </ColumnLayout>
      <SpaceBetween size="m">
        <Box variant="h3">Workbench owner permissions</Box>
        <Toggle data-test="project-shutdown-allow-nightly" checked={policy.allowUserDisableNightlyStop}
          disabled={stopDisabled}
          onChange={({ detail }) => setPolicy({ ...policy, allowUserDisableNightlyStop: detail.checked })}>
          Allow owners to disable nightly shutdown for their workbench
        </Toggle>
        <Toggle data-test="project-shutdown-allow-idle"
          checked={policy.allowUserIdleTimeout} disabled={stopDisabled}
          onChange={({ detail }) => setPolicy({ ...policy, allowUserIdleTimeout: detail.checked })}>
          Allow owners to change their workbench idle timeout
        </Toggle>
        <ColumnLayout columns={2}>
          <FormField label="Minimum owner timeout (minutes)"
            constraintText="Whole number from 10 to 1440." errorText={minError}>
            <Input data-test="project-shutdown-min" ariaLabel="Minimum owner timeout in minutes"
              type="number" inputMode="numeric" value={minimum}
              disabled={stopDisabled || !policy.allowUserIdleTimeout}
              onChange={({ detail }) => setMinimum(detail.value)} />
          </FormField>
          <FormField label="Maximum owner timeout (minutes)"
            constraintText="Must be at least the minimum timeout, up to 1440." errorText={maxError}>
            <Input data-test="project-shutdown-max" ariaLabel="Maximum owner timeout in minutes"
              type="number" inputMode="numeric" value={maximum}
              disabled={stopDisabled || !policy.allowUserIdleTimeout}
              onChange={({ detail }) => setMaximum(detail.value)} />
          </FormField>
        </ColumnLayout>
      </SpaceBetween>
      <div>
        <Box variant="h3">{dirty ? 'Policy preview (save to apply)' : 'Effective project defaults'}</Box>
        <Box variant="p">Individual workbenches can use owner overrides where this policy allows them.</Box>
        <ColumnLayout columns={3} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Idle shutdown</Box>
            <div>{idleSummary}</div>
          </div>
          <div>
            <Box variant="awsui-key-label">Nightly shutdown</Box>
            <div>{onOff(effective.nightlyEnabled)} ({nightlySource})</div>
          </div>
          <div>
            <Box variant="awsui-key-label">Weekend shutdown</Box>
            <div>
              {onOff(effective.weekendEnabled)} ({weekendSource})
            </div>
          </div>
        </ColumnLayout>
      </div>
      {response.canEdit && <SpaceBetween size="s" direction="horizontal">
        <Button variant="primary" loading={busy} disabled={!dirty || invalid} onClick={() => mutate(false)}>
          Save policy
        </Button>
        <Button disabled={busy || !dirty} onClick={() => adopt(response)}>Discard changes</Button>
        <Button disabled={busy || !response.settings && !dirty} onClick={() => mutate(true)}>
          Reset to platform defaults
        </Button>
      </SpaceBetween>}
      <Box variant="small">
        Reset removes the program policy, inherits all platform stop defaults,
        and removes owner override permissions.
      </Box>
    </SpaceBetween>
  </Container>;
}
