// The header's program selector: programs sorted by name, searchable by name and id.
import { SelectProps } from '@cloudscape-design/components';
import { Project } from '../../../state';

export const PROGRAMS_OPTION = 'programs';
export const PROGRAM_OPTION_PREFIX = 'project-';

type ProgramLike = Pick<Project, 'id' | 'name' | 'isActive'>;

// Case- and accent-insensitive, then by id so equal names keep a fixed order.
export function sortProgramsByName<T extends ProgramLike>(programs: readonly T[]): T[] {
  return [...programs].sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }) || a.id.localeCompare(b.id));
}

export function programOptions(
  programs: readonly ProgramLike[],
  i18n: { programs: string, inactive: string },
): SelectProps.Option[] {
  return [
    { value: PROGRAMS_OPTION, label: i18n.programs, iconName: 'settings' },
    ...sortProgramsByName(programs).map(p => ({
      value: `${PROGRAM_OPTION_PREFIX}${p.id}`,
      label: p.name || p.id,
      // The search matches the id too, without showing it.
      filteringTags: [p.id],
      tags: p.isActive ? undefined : [i18n.inactive],
    })),
  ];
}
