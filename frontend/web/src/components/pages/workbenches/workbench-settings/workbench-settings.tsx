import { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  ColumnLayout,
  Container,
  FormField,
  Header,
  Input,
  SpaceBetween,
  Toggle,
} from '@cloudscape-design/components';
import {
  WorkbenchLifecycleResponse,
  WorkbenchLifecycleUserSettings,
} from '../../../../services/API/proserve-wb-provisioning-api';
import { provisioningAPI } from '../../../../services/API/provisioning-api';
import { extractErrorResponseMessage } from '../../../../utils/api-helpers';
import { i18nWorkbenchSettings as i18n } from './translations';

type LoadLifecycle = (projectId: string, provisionedProductId: string) => Promise<WorkbenchLifecycleResponse>;
type SaveLifecycle = (
  projectId: string,
  provisionedProductId: string,
  settings: WorkbenchLifecycleUserSettings,
) => Promise<WorkbenchLifecycleResponse>;

interface WorkbenchSettingsPanelProps {
  projectId: string,
  provisionedProductId: string,
  load: LoadLifecycle,
  save: SaveLifecycle,
}

const NONE = 0;

function onOff(value: boolean) {
  return value ? i18n.on : i18n.off;
}

/**
 * The workbench's stop rules. What the owner may change is decided by the project's workbench stop
 * policy; the backend refuses anything else, so the card only offers what the project allows.
 * Hidden when the settings cannot be loaded.
 */
export function WorkbenchSettingsPanel(props: WorkbenchSettingsPanelProps) {
  const { projectId, provisionedProductId, load, save } = props;
  const key = `${projectId}/${provisionedProductId}`;
  const [loaded, setLoaded] = useState<{ key: string, lifecycle?: WorkbenchLifecycleResponse }>();
  const [nightlyStopDisabled, setNightlyStopDisabled] = useState(false);
  const [idleTimeout, setIdleTimeout] = useState('');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error', text: string }>();

  function adopt(lifecycle: WorkbenchLifecycleResponse) {
    setLoaded({ key, lifecycle });
    setNightlyStopDisabled(!!lifecycle.userSettings.nightlyStopDisabled);
    const minutes = lifecycle.userSettings.idleTimeoutMinutes;
    setIdleTimeout(minutes === null || minutes === undefined ? '' : String(minutes));
  }

  useEffect(() => {
    let active = true;
    // Keyed by workbench, so switching workbenches never shows the previous answer.
    load(projectId, provisionedProductId)
      .then(lifecycle => {
        if (active) {
          adopt(lifecycle);
        }
      })
      .catch(() => {
        if (active) {
          setLoaded({ key });
        }
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, load]);

  const lifecycle = loaded?.key === key ? loaded.lifecycle : undefined;
  if (!lifecycle) {
    return null;
  }

  const { permissions, effective, canEdit } = lifecycle;
  const min = permissions.idleTimeoutMinMinutes;
  const max = permissions.idleTimeoutMaxMinutes;
  const idleValue = idleTimeout.trim() === '' ? null : Number(idleTimeout);
  const idleInvalid = idleValue !== null &&
    (!Number.isInteger(idleValue) || idleValue < min || idleValue > max);

  async function handleSave() {
    setSaving(true);
    setMessage(undefined);
    try {
      const updated = await save(projectId, provisionedProductId, {
        nightlyStopDisabled: permissions.mayDisableNightlyStop ? nightlyStopDisabled : false,
        idleTimeoutMinutes: permissions.maySetIdleTimeout ? idleValue : null,
      });
      adopt(updated);
      setMessage({ type: 'success', text: i18n.saved });
    } catch (error) {
      const text = await extractErrorResponseMessage(error);
      setMessage({ type: 'error', text: `${i18n.saveError}${text ? `: ${text}` : ''}` });
    } finally {
      setSaving(false);
    }
  }

  function source(name: string) {
    const from = effective.sources[name];
    return from ? ` (${i18n.sources[from] ?? from})` : '';
  }

  function renderControls() {
    if (effective.alwaysOn) {
      return <Alert type="info">{i18n.alwaysOn}</Alert>;
    }
    const editable = canEdit && (permissions.mayDisableNightlyStop || permissions.maySetIdleTimeout);
    return (
      <SpaceBetween size="m">
        <FormField
          label={i18n.nightlyStopLabel}
          description={
            i18n.nightlyStopDescription(lifecycle!.nightlyStopTime, lifecycle!.nightlyStopTimezone)
          }
          constraintText={permissions.mayDisableNightlyStop ? undefined : i18n.notAllowed}
        >
          <Toggle
            data-test="workbench-settings-nightly"
            checked={nightlyStopDisabled}
            disabled={!canEdit || !permissions.mayDisableNightlyStop}
            onChange={({ detail }) => setNightlyStopDisabled(detail.checked)}
          />
        </FormField>
        <FormField
          label={i18n.idleTimeoutLabel}
          description={i18n.idleTimeoutDescription(min, max)}
          constraintText={permissions.maySetIdleTimeout ? undefined : i18n.notAllowed}
          errorText={idleInvalid ? i18n.idleTimeoutOutOfBounds(min, max) : undefined}
        >
          <Input
            data-test="workbench-settings-idle"
            type="number"
            inputMode="numeric"
            value={idleTimeout}
            placeholder={String(effective.idleStopMinutes)}
            disabled={!canEdit || !permissions.maySetIdleTimeout}
            onChange={({ detail }) => setIdleTimeout(detail.value)}
          />
        </FormField>
        {editable ?
          <SpaceBetween size="s" direction="horizontal" alignItems="center">
            <Button
              data-test="workbench-settings-save"
              variant="primary"
              loading={saving}
              disabled={idleInvalid}
              onClick={handleSave}
            >
              {i18n.save}
            </Button>
            <Box variant="small">{i18n.costNotice}</Box>
          </SpaceBetween>
          : null}
        {!canEdit ? <Box variant="small">{i18n.readOnly}</Box> : null}
      </SpaceBetween>
    );
  }

  return (
    <Container
      data-test="workbench-settings"
      header={<Header variant="h2" description={i18n.description}>{i18n.header}</Header>}
    >
      <SpaceBetween size="l">
        {message ? <Alert type={message.type}>{message.text}</Alert> : null}
        {renderControls()}
        <div>
          <Box variant="h4">{i18n.effectiveHeader}</Box>
          <ColumnLayout columns={3} variant="text-grid">
            <div>
              <Box variant="awsui-key-label">{i18n.effectiveIdle}</Box>
              <div data-test="workbench-settings-effective-idle">
                {effective.idleStopEnabled ? i18n.effectiveIdleValue(effective.idleStopMinutes) : i18n.off}
                {source('idleStopMinutes')}
              </div>
            </div>
            <div>
              <Box variant="awsui-key-label">{i18n.effectiveNightly}</Box>
              <div data-test="workbench-settings-effective-nightly">
                {onOff(effective.nightlyStop)}{source('nightlyStop')}
              </div>
            </div>
            <div>
              <Box variant="awsui-key-label">{i18n.effectiveWeekend}</Box>
              <div>{onOff(effective.weekendStop)}{source('weekendStop')}</div>
            </div>
          </ColumnLayout>
          {effective.ignoredUserSettings.length > NONE ? <Box variant="small">{i18n.ignored}</Box> : null}
        </div>
      </SpaceBetween>
    </Container>
  );
}

interface WorkbenchSettingsProps {
  projectId?: string,
  provisionedProductId?: string,
}

export function WorkbenchSettings({ projectId, provisionedProductId }: WorkbenchSettingsProps) {
  if (!projectId || !provisionedProductId) {
    return null;
  }
  return <WorkbenchSettingsPanel
    projectId={projectId}
    provisionedProductId={provisionedProductId}
    load={provisioningAPI.getProvisionedProductLifecycle}
    save={provisioningAPI.updateProvisionedProductLifecycle}
  />;
}
