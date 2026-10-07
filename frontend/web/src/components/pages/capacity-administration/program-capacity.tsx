// Program admins see their own program's capacity - its account's quotas,
// what is left, how many workbenches of each size still fit, and every workbench by size. Requesting
// more quota stays with platform admins.
import { useEffect, useMemo, useState } from 'react';
import { Alert, Box, ContentLayout, Header, SpaceBetween, Table } from '@cloudscape-design/components';
import { useRecoilValue } from 'recoil';
import { WorkbenchAppLayout } from '../../layout/workbench-app-layout/workbench-app-layout';
import { selectedProjectState } from '../../../state';
import { capacityAPI, ProjectCapacity, ProjectCapacityWorkbenches } from '../../../services/API/capacity-api';
import { i18n } from './translations';
import { fitsPerSize, projectQuotaRows } from './logic';
import { QuotasTable, WorkbenchesTable } from './components';

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function useProgramCapacity(projectId?: string) {
  const [capacity, setCapacity] = useState<ProjectCapacity>();
  const [workbenches, setWorkbenches] = useState<ProjectCapacityWorkbenches>();
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    if (!projectId) {
      return;
    }
    Promise.all([capacityAPI.getProjectCapacity(projectId, []), capacityAPI.getProjectWorkbenches(projectId)])
      .then(([c, w]) => {
        setCapacity(c);
        setWorkbenches(w);
      })
      .catch(e => setError(errorText(e)))
      .finally(() => setLoading(false));
  }, [projectId]);
  return { capacity, workbenches, error, loading };
}

export function ProgramCapacity() {
  const project = useRecoilValue(selectedProjectState);
  const { capacity, workbenches, error, loading } = useProgramCapacity(project.projectId);
  const rows = useMemo(
    () => projectQuotaRows(capacity, project.projectName), [capacity, project.projectName]
  );
  const fits = useMemo(() => fitsPerSize(capacity), [capacity]);
  return (
    <WorkbenchAppLayout
      breadcrumbItems={[{ path: i18n.programBreadcrumb, href: '#' }]}
      content={
        <ContentLayout header={<Header variant="h1" description={i18n.programDescription}>
          {i18n.programTitle(project.projectName)}</Header>}>
          <SpaceBetween size="m">
            {error && <Alert type="error">{error}</Alert>}
            <QuotasTable rows={rows} loading={loading} />
            <Table
              header={<Header variant="h2" description={i18n.fitsDescription}>{i18n.fits}</Header>}
              items={fits}
              trackBy="family"
              empty={<Box textAlign="center">{i18n.noData}</Box>}
              columnDefinitions={[
                { id: 'family', header: i18n.quota, cell: r => r.family },
                { id: 's', header: i18n.sizeS, cell: r => r.small ?? '-' },
                { id: 'm', header: i18n.sizeM, cell: r => r.medium ?? '-' },
                { id: 'l', header: i18n.sizeL, cell: r => r.large ?? '-' },
              ]}
            />
            <WorkbenchesTable workbenches={workbenches} programChosen picker={null} />
          </SpaceBetween>
        </ContentLayout>
      }
    />
  );
}

export default ProgramCapacity;
