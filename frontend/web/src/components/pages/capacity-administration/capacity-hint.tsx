// Next to the launch form's size, how many more workbenches of that size fit the program's
// account. Shows nothing without data (the launch is then not refused on capacity either).
//
// It counts the account's vCPU quota only, not AWS's free machines in a zone, which can't be known
// before a start. Worded so it doesn't read as a promise that the size is available (a user saw
// "Capacity for 3 more", then the start failed with no capacity in the zone).
import { useEffect, useState } from 'react';
import { StatusIndicator } from '@cloudscape-design/components';
import { capacityAPI, capacityHint, ProjectCapacity } from '../../../services/API/capacity-api';

const NONE_LEFT = 0;

const capacityHintTexts = {
  noRoom: 'The program\'s quota has no room for this size. Choose a smaller size or ask a platform admin.',
  room: (available: number) => `Room in the program's quota for ${available} more of this size. AWS's free ` +
    'capacity in a zone is only known when the workbench starts.',
};

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
    return <StatusIndicator type="error">{capacityHintTexts.noRoom}</StatusIndicator>;
  }
  return <StatusIndicator type="success">{capacityHintTexts.room(available)}</StatusIndicator>;
}
