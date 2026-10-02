/* eslint-disable @typescript-eslint/no-magic-numbers */
import { render } from '@testing-library/react';
import { RecipeVersionWizardStep1, RecipeVersionWizardStep1Props } from './recipe-version-wizard-step1';

const props: RecipeVersionWizardStep1Props = {
  isUpdate: false,
  description: 'build image',
  setDescription: () => undefined,
  isDescriptionValid: true,
  volumeSize: 100,
  setVolumeSize: () => undefined,
  isVolumeSizeValid: true,
  minVolumeSize: 8,
  maxVolumeSize: 500,
  versionReleaseTypes: ['MAJOR'],
  versionReleaseType: 'MAJOR',
  setVersionReleaseType: () => undefined,
  isVersionReleaseTypeValid: true,
  availableIntegrations: [],
  selectedIntegrations: [],
  setSelectedIntegrations: () => undefined,
  isIntegrationsLoading: false,
};

const CHANNEL_SELECT = '[data-test="recipe-version-base-image-channel"]';

describe('RecipeVersionWizardStep1 base image channel', () => {
  it('offers the channel on a base image recipe', () => {
    const { container } = render(
      <RecipeVersionWizardStep1 {...props} hasBaseImageChannel baseImageChannel="test" />
    );
    expect(container.querySelector(CHANNEL_SELECT)?.textContent).toContain('test');
  });

  it('hides the channel on other recipes', () => {
    const { container } = render(<RecipeVersionWizardStep1 {...props} />);
    expect(container.querySelector(CHANNEL_SELECT)).toBeNull();
  });
});
