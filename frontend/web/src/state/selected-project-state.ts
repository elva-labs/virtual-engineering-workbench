import { atom } from 'recoil';

export type SelectedProject = {
  projectId?: string,
  projectName?: string,
  projectDescription?: string,
  isActive?: boolean,
  roles?: string[],
  // 'workbench-only': its members (but admins) see only their workbenches.
  experience?: string,
};


const selectedProjectState = atom<SelectedProject>({
  key: 'selected-project',
  default: {}
});

export { selectedProjectState };
