// The header's program selector: a Cloudscape Select with its own filtering (sorted by name,
// searchable by name and id) instead of the plain ButtonDropdown.
import { Select, SelectProps } from '@cloudscape-design/components';
import { Project } from '../../../state';
import { PROGRAMS_OPTION, PROGRAM_OPTION_PREFIX, programOptions } from './program-select.logic';
import styles from './program-select.module.scss';

const i18n = {
  programs: 'Programs',
  inactive: 'Inactive',
  filteringPlaceholder: 'Find a program',
  filteringAriaLabel: 'Find a program by name or id',
  noMatch: 'No programs found',
  ariaLabel: 'Program',
};

type Props = {
  programs: readonly Project[],
  selectedProgramId?: string,
  triggerText: string,
  onSelectProgram: (programId: string) => void,
  onSelectProgramsPage: () => void,
};

export function ProgramSelect({
  programs, selectedProgramId, triggerText, onSelectProgram, onSelectProgramsPage,
}: Props): JSX.Element {
  const options = programOptions(programs, i18n);
  const selected = options.find(o => o.value === `${PROGRAM_OPTION_PREFIX}${selectedProgramId}`);

  function onChange({ detail }: { detail: SelectProps.ChangeDetail }) {
    const value = detail.selectedOption.value ?? '';
    if (value === PROGRAMS_OPTION) {
      onSelectProgramsPage();
    } else if (value.startsWith(PROGRAM_OPTION_PREFIX)) {
      onSelectProgram(value.substring(PROGRAM_OPTION_PREFIX.length));
    }
  }

  return (
    <div className={styles.programSelect} title={triggerText}>
      <Select
        data-test="custom-drop-down-program"
        // The trigger shows the header's text (a program, "Programs", or a loading text), not
        // necessarily the selected option.
        selectedOption={{ ...selected, label: triggerText, tags: undefined }}
        options={options}
        onChange={onChange}
        filteringType="auto"
        filteringPlaceholder={i18n.filteringPlaceholder}
        filteringAriaLabel={i18n.filteringAriaLabel}
        noMatch={i18n.noMatch}
        ariaLabel={i18n.ariaLabel}
        expandToViewport
      />
    </div>
  );
}
