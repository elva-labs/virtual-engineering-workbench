import { ReactNode } from 'react';
import {
  Box,
  Button,
  ColumnLayout,
  Container,
  Header,
  ProgressBar,
  StatusIndicator,
  Table,
  TableProps,
} from '@cloudscape-design/components';
import {
  CapacityOverview,
  CapacityQuota,
  CapacityWorkbench,
  ProjectCapacityWorkbenches,
} from '../../../services/API/capacity-api';
import { i18n } from './translations';
import { DEFAULT_ALARM_PERCENT, overviewTiles, QuotaRow, quotaState } from './logic';

const USE_COLUMN_WIDTH = 220;
const NO_USE = 0;
const TILE_COLUMNS = 6;

function QuotaUse({ quota, alarmPercent }: { quota: CapacityQuota, alarmPercent: number }) {
  const state = quotaState(quota, alarmPercent);
  if (state === 'unknown') {
    return <StatusIndicator type="info">{i18n.unknown}</StatusIndicator>;
  }
  if (state === 'unavailable') {
    return <StatusIndicator type="stopped">{i18n.notAvailable(quota.used)}</StatusIndicator>;
  }
  return (
    <ProgressBar
      value={quota.usedPercent ?? NO_USE}
      status={state === 'high' ? 'error' : 'in-progress'}
      additionalInfo={`${quota.used} / ${quota.limit} ${quota.unit}`}
    />
  );
}

export function OverviewTiles({ overview }: { overview?: CapacityOverview }) {
  const tiles = overviewTiles(overview);
  return (
    <Container header={<Header variant="h2" description={i18n.collectedAt(overview?.collectedAt)}>
      {i18n.overview}</Header>}>
      <ColumnLayout columns={TILE_COLUMNS} variant="text-grid">
        {tiles.map(t =>
          <div key={t.label}>
            <Box variant="awsui-key-label">{t.label}</Box>
            <Box variant="h2">{t.value}</Box>
          </div>)}
      </ColumnLayout>
    </Container>
  );
}

function openRequestText(r: QuotaRow): string {
  return r.openRequest ? `${r.openRequest.status}: ${r.openRequest.desiredValue} ${r.quota.unit}` : '-';
}

function quotaColumns(
  alarm: number, onRequest?: (row: QuotaRow) => void
): TableProps.ColumnDefinition<QuotaRow>[] {
  const columns: TableProps.ColumnDefinition<QuotaRow>[] = [
    { id: 'program', header: i18n.program, cell: r => r.programs },
    { id: 'account', header: i18n.account, cell: r => `${r.awsAccountId} (${r.region})` },
    { id: 'quota', header: i18n.quota, cell: r => `${r.quota.label} (${r.quota.quotaCode})` },
    {
      id: 'use', header: i18n.use, minWidth: USE_COLUMN_WIDTH,
      cell: r => <QuotaUse quota={r.quota} alarmPercent={alarm} />,
    },
    { id: 'remaining', header: i18n.remaining, cell: r => r.quota.remaining ?? '-' },
  ];
  if (!onRequest) {
    // Program admins see their capacity; requesting more is a platform admin's action.
    return columns;
  }
  return [
    ...columns,
    { id: 'request', header: i18n.openRequest, cell: openRequestText },
    {
      id: 'action', header: '',
      cell: r => <Button variant="inline-link" onClick={() => onRequest(r)}>{i18n.requestIncrease}</Button>,
    },
  ];
}

export function QuotasTable({ rows, loading, alarmPercent, onRequest }: {
  rows: QuotaRow[],
  loading: boolean,
  alarmPercent?: number,
  onRequest?: (row: QuotaRow) => void,
}) {
  return (
    <Table
      loading={loading}
      header={<Header variant="h2" description={i18n.quotasDescription}>{i18n.quotas}</Header>}
      items={rows}
      trackBy={r => `${r.awsAccountId}/${r.region}/${r.quota.quotaCode}`}
      empty={<Box textAlign="center">{i18n.noData}</Box>}
      columnDefinitions={quotaColumns(alarmPercent ?? DEFAULT_ALARM_PERCENT, onRequest)}
    />
  );
}

export function InstanceTypesTable({ overview }: { overview?: CapacityOverview }) {
  const items = Object.entries(overview?.totals?.runningByInstanceType ?? {})
    .map(([instanceType, count]) => ({ instanceType, count }));
  return (
    <Table
      header={<Header variant="h2">{i18n.instanceTypes}</Header>}
      items={items}
      trackBy="instanceType"
      empty={<Box textAlign="center">{i18n.noneRunning}</Box>}
      columnDefinitions={[
        { id: 'type', header: i18n.instanceType, cell: r => r.instanceType },
        { id: 'count', header: i18n.runningCount, cell: r => r.count },
      ]}
    />
  );
}

function since(w: CapacityWorkbench): string {
  return w.startDate ? new Date(w.startDate).toLocaleString() : '-';
}

const WORKBENCH_COLUMNS: TableProps.ColumnDefinition<CapacityWorkbench>[] = [
  { id: 'owner', header: i18n.owner, cell: w => w.owner },
  { id: 'product', header: i18n.product, cell: w => `${w.productName} ${w.versionName}` },
  { id: 'stage', header: i18n.stage, cell: w => w.stage },
  { id: 'size', header: i18n.size, cell: w => w.instanceType ?? '-' },
  { id: 'disk', header: i18n.disk, cell: w => w.volumeSize ? `${w.volumeSize} GB` : '-' },
  { id: 'status', header: i18n.status, cell: w => w.status },
  { id: 'since', header: i18n.since, cell: since },
  {
    id: 'idle', header: i18n.idleTimeout,
    cell: w => w.idleTimeoutMinutes ? `${w.idleTimeoutMinutes} min` : i18n.programDefault,
  },
];

export function WorkbenchesTable({ workbenches, programChosen, picker }: {
  workbenches?: ProjectCapacityWorkbenches,
  programChosen: boolean,
  picker: ReactNode,
}) {
  return (
    <Table
      header={
        <Header variant="h2" description={i18n.drillDownDescription} actions={picker}>
          {i18n.drillDown}
        </Header>
      }
      items={workbenches?.workbenches ?? []}
      trackBy="provisionedProductId"
      empty={<Box textAlign="center">{programChosen ? i18n.noWorkbenches : i18n.chooseProgram}</Box>}
      columnDefinitions={WORKBENCH_COLUMNS}
    />
  );
}
