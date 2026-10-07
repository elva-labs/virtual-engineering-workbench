import { isGroupOnly, withGroupMembers } from './project-users.logic';

// Members lists direct assignments and members through a bound group (since their sign-in).
describe('withGroupMembers', () => {
  const direct = { userId: 'U1', userEmail: 'u1@example.com', roles: ['PROGRAM_OWNER'] };
  const viaGroup = (userId: string) => ({
    userId, userEmail: `${userId.toLowerCase()}@example.com`, groupIds: ['g-users'], roles: ['PLATFORM_USER'],
  });

  it('adds group-only members as rows that are managed in Entra', () => {
    const rows = withGroupMembers([direct], [viaGroup('U2')]);
    expect(rows.map(r => r.userId)).toEqual(['U1', 'U2']);
    expect(rows.map(isGroupOnly)).toEqual([false, true]);
    expect(rows[1]).toMatchObject({ roles: ['PLATFORM_USER'], viaGroupIds: ['g-users'] });
  });

  it('keeps a direct assignment and notes the group for a member who has both', () => {
    const rows = withGroupMembers([direct], [viaGroup('U1')]);
    expect(rows.map(r => r.userId)).toEqual(['U1']);
    expect(rows[0]).toMatchObject({ roles: ['PROGRAM_OWNER'], viaGroupIds: ['g-users'] });
    expect(isGroupOnly(rows[0])).toBe(false);
  });
});
