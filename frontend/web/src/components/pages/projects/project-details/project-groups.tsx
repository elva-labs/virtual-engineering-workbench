import { Badge, Button, Header, SpaceBetween, Table } from '@cloudscape-design/components';
import type { ProjectGroupAssignment } from '../../../../services/API/projects-api';

type Props = {
  assignments: ProjectGroupAssignment[],
  onRefresh: () => void,
};

export function ProjectGroups({ assignments, onRefresh }: Props) {
  return <Table
    header={<Header
      counter={`(${assignments.length})`}
      actions={<Button iconName="refresh" onClick={onRefresh}/>}
    >Project group grants</Header>}
    columnDefinitions={[
      { id: 'groupId', header: 'Entra group ID', cell: item => item.groupId },
      { id: 'groupName', header: 'Group name', cell: item => item.groupName || '—' },
      {
        id: 'roles', header: 'Project roles', cell: item => <SpaceBetween direction="horizontal" size="xs">
          {item.roles.map(role => <Badge color="blue" key={role}>{role}</Badge>)}
        </SpaceBetween>
      }
    ]}
    items={assignments}
    trackBy="groupId"
    empty="No group grants for this project."
  />;
}
