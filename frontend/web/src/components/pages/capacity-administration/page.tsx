// Spoke capacity: platform admins see every program's accounts, quotas and workbenches,
// and request more quota (GPU quotas are 0 until requested).
import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  ContentLayout,
  FormField,
  Header,
  Input,
  Modal,
  Select,
  SelectProps,
  SpaceBetween,
} from '@cloudscape-design/components';
import { WorkbenchAppLayout } from '../../layout/workbench-app-layout/workbench-app-layout';
import {
  capacityAPI,
  CapacityOverview,
  ProjectCapacityWorkbenches,
} from '../../../services/API/capacity-api';
import { i18n } from './translations';
import { programOptions, QuotaRow, quotaRows, suggestedTotal } from './logic';
import { InstanceTypesTable, OverviewTiles, QuotasTable, WorkbenchesTable } from './components';

type Notice = { type: 'success' | 'error', text: string };

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function RequestModal({ row, onClose, onDone }: {
  row: QuotaRow | null,
  onClose: () => void,
  onDone: (notice: Notice) => void,
}) {
  const [desired, setDesired] = useState('');
  useEffect(() => {
    setDesired(row ? String(suggestedTotal(row.quota)) : '');
  }, [row]);
  const submit = () => {
    if (!row) {
      return;
    }
    capacityAPI.requestIncrease(row.awsAccountId, row.quota.quotaCode, Number(desired), row.region)
      .then(r => onDone({ type: 'success', text: i18n.requested(r.status, r.requestId) }))
      .catch(e => onDone({ type: 'error', text: errorText(e) }));
  };
  const tooLow = !desired || Number(desired) <= (row?.quota.limit ?? Number.NEGATIVE_INFINITY);
  return (
    <Modal
      visible={row !== null}
      onDismiss={onClose}
      header={i18n.requestIncrease}
      footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs">
        <Button variant="link" onClick={onClose}>{i18n.cancel}</Button>
        <Button variant="primary" onClick={submit} disabled={tooLow}>{i18n.submit}</Button>
      </SpaceBetween></Box>}
    >
      {row &&
        <SpaceBetween size="s">
          <Box>{i18n.requestExplanation(row.quota.label, row.awsAccountId, row.quota.limit)}</Box>
          <FormField label={i18n.newTotal(row.quota.unit)}>
            <Input type="number" value={desired} onChange={e => setDesired(e.detail.value)} />
          </FormField>
        </SpaceBetween>}
    </Modal>
  );
}

function useOverview() {
  const [overview, setOverview] = useState<CapacityOverview>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const load = () => {
    setLoading(true);
    capacityAPI.getOverview()
      .then(setOverview)
      .catch(e => setError(errorText(e)))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);
  return { overview, loading, error, setError, load };
}

function useWorkbenches(projectId: string | undefined, onError: (text: string) => void) {
  const [workbenches, setWorkbenches] = useState<ProjectCapacityWorkbenches>();
  useEffect(() => {
    if (!projectId) {
      return;
    }
    capacityAPI.getProjectWorkbenches(projectId).then(setWorkbenches).catch(e => onError(errorText(e)));
  }, [projectId]);
  return projectId ? workbenches : undefined;
}

export function CapacityAdministration() {
  const { overview, loading, error, setError, load } = useOverview();
  const [program, setProgram] = useState<SelectProps.Option | null>(null);
  const [requestFor, setRequestFor] = useState<QuotaRow | null>(null);
  const [notice, setNotice] = useState<Notice>();
  const workbenches = useWorkbenches(program?.value, setError);
  const rows = useMemo(() => quotaRows(overview), [overview]);
  const options = useMemo(() => programOptions(overview), [overview]);

  const header =
    <Header variant="h1" description={i18n.description}
      actions={<Button iconName="refresh" onClick={load} loading={loading}>{i18n.refresh}</Button>}>
      {i18n.title}
    </Header>;
  const programPicker =
    <Select selectedOption={program} options={options} placeholder={i18n.chooseProgram}
      onChange={e => setProgram(e.detail.selectedOption)} />;

  return (
    <WorkbenchAppLayout
      breadcrumbItems={[{ path: i18n.breadcrumb, href: '#' }]}
      content={
        <ContentLayout header={header}>
          <SpaceBetween size="m">
            {error && <Alert type="error" dismissible onDismiss={() => setError(undefined)}>{error}</Alert>}
            {notice &&
              <Alert type={notice.type} dismissible onDismiss={() => setNotice(undefined)}>
                {notice.text}
              </Alert>}
            <OverviewTiles overview={overview} />
            <QuotasTable rows={rows} loading={loading} alarmPercent={overview?.alarmUsedPercent}
              onRequest={setRequestFor} />
            <InstanceTypesTable overview={overview} />
            <WorkbenchesTable workbenches={workbenches} programChosen={!!program} picker={programPicker} />
          </SpaceBetween>
          <RequestModal row={requestFor} onClose={() => setRequestFor(null)} onDone={n => {
            setNotice(n);
            setRequestFor(null);
            load();
          }} />
        </ContentLayout>
      }
    />
  );
}

export default CapacityAdministration;
