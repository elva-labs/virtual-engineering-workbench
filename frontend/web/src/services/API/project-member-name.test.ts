import { describe, expect, it } from 'vitest';
import {
  GetProjectAssignmentsResponseItemFromJSON,
  GetProjectAssignmentsResponseItemToJSON
} from './proserve-wb-projects-api';

describe('project member display name', () => {
  it('reads and sends optional persisted assignment metadata', () => {
    const member = GetProjectAssignmentsResponseItemFromJSON({
      userId: 'USER-1',
      userEmail: 'new.member@example.com',
      userDisplayName: 'New Member',
      roles: ['PLATFORM_USER']
    });
    expect(member.userDisplayName).toBe('New Member');
    expect(GetProjectAssignmentsResponseItemToJSON(member).userDisplayName).toBe('New Member');
  });
});
