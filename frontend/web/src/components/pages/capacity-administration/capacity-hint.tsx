// Next to the launch form's size, how many more workbenches of that size fit the program's
// account. Shows nothing without data (the launch is then not refused on capacity either).
import { useEffect, useState } from 'react';
import { StatusIndicator } from '@cloudscape-design/components';
import { capacityAPI, capacityHint, ProjectCapacity } from '../../../services/API/capacity-api';

const NONE_LEFT = 0;

export function CapacityHint({ projectId, instanceType, instanceTypes }: {
  projectId?: string,
  instanceType?: string,
  instanceTypes: string[],
}) {
  const [capacity, setCapacity] = useState<ProjectCapacity>();
  const key = instanceTypes.join(',');
  useEffect(() => {
    if (!projectId || !key) {
      return;
    }
    capacityAPI.getProjectCapacity(projectId, key.split(','))
      .then(setCapacity)
      .catch(() => setCapacity(undefined));
  }, [projectId, key]);

  if (!instanceType) {
    return null;
  }
  const { available } = capacityHint(capacity, instanceType);
  if (available === null) {
    return null;
  }
  if (available === NONE_LEFT) {
    return (
      <StatusIndicator type="error">
        No capacity for this size in the program's account. Choose a smaller size or ask a platform admin.
      </StatusIndicator>
    );
  }
  return <StatusIndicator type="success">{`Capacity for ${available} more of this size`}</StatusIndicator>;
}
