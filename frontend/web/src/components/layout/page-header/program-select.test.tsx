/* eslint-disable @typescript-eslint/no-magic-numbers */
// The header's program selector: sorted by name, searchable by name and id.
import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import createWrapper from '@cloudscape-design/components/test-utils/dom';
import { Project } from '../../../state';
import { ProgramSelect } from './program-select';
import { programOptions, sortProgramsByName } from './program-select.logic';

// The dropdown renders in a portal (expandToViewport).
const E = { expandToViewport: true };

function program(id: string, name: string, isActive = true): Project {
  return { id, name, description: '', isActive };
}

const programs = [
  program('proj-3', 'clac0001481'),
  program('proj-1', 'Shared'),
  program('proj-4', 'aiplatform'),
  program('proj-2', 'Ålesund', false),
  program('proj-5', 'shared'),
];

describe('sortProgramsByName', () => {
  it('sorts case- and accent-insensitively, then by id', () => {
    expect(sortProgramsByName(programs).map(p => p.id))
      .toEqual(['proj-4', 'proj-2', 'proj-3', 'proj-1', 'proj-5']);
  });

  it('does not change its input', () => {
    const input = [...programs];
    sortProgramsByName(input);
    expect(input).toEqual(programs);
  });
});

describe('programOptions', () => {
  it('lists the programs page first, then the programs with their id as a hidden search tag', () => {
    const i18n = { programs: 'Programs', inactive: 'Inactive' };
    const options = programOptions([program('proj-9', 'Zeta', false)], i18n);
    expect(options[0]).toMatchObject({ value: 'programs', label: 'Programs' });
    expect(options[1]).toEqual({
      value: 'project-proj-9', label: 'Zeta', filteringTags: ['proj-9'], tags: ['Inactive'],
    });
  });
});

function renderSelect(onSelectProgram = vi.fn(), onSelectProgramsPage = vi.fn()) {
  render(
    <ProgramSelect programs={programs} selectedProgramId="proj-1" triggerText="Shared"
      onSelectProgram={onSelectProgram} onSelectProgramsPage={onSelectProgramsPage} />
  );
  const select = createWrapper().findSelect('[data-test="custom-drop-down-program"]')!;
  return { select, onSelectProgram, onSelectProgramsPage };
}

function labels(select: ReturnType<typeof renderSelect>['select']): (string | null)[] {
  return select.findDropdown(E).findOptions().map(o => o.findLabel().getElement().textContent);
}

describe('ProgramSelect', () => {
  it('shows the current program and lists the programs by name', () => {
    const { select } = renderSelect();
    expect(select.findTrigger().getElement().textContent).toContain('Shared');
    select.openDropdown();
    expect(labels(select)).toEqual(['Programs', 'aiplatform', 'Ålesund', 'clac0001481', 'Shared', 'shared']);
  });

  it('filters by name and by id, and says so when nothing matches', () => {
    const { select } = renderSelect();
    select.openDropdown();
    select.findFilteringInput(E)!.setInputValue('clac');
    expect(labels(select)).toEqual(['clac0001481']);
    select.findFilteringInput(E)!.setInputValue('proj-4');
    expect(labels(select)).toEqual(['aiplatform']);
    select.findFilteringInput(E)!.setInputValue('nothing like this');
    expect(select.findDropdown(E).findOptions()).toHaveLength(0);
    expect(select.findDropdown(E).getElement().textContent).toContain('No programs found');
  });

  it('switches program, or opens the programs page', () => {
    const { select, onSelectProgram, onSelectProgramsPage } = renderSelect();
    select.openDropdown();
    select.selectOptionByValue('project-proj-3', E);
    expect(onSelectProgram).toHaveBeenCalledWith('proj-3');
    select.openDropdown();
    select.selectOptionByValue('programs', E);
    expect(onSelectProgramsPage).toHaveBeenCalled();
  });
});
