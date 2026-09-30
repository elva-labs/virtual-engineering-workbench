// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { Amplify } from 'aws-amplify';
import {
  DefaultApi,
  Configuration,
  GetProjectAccountsResponse,
  GetProjectsResponse,
  OnBoardProjectAccountRequest,
  GetProjectAssignmentsResponse,
  GetUserRolesResponse,
  AssignUserRequest,
  ReAssignUsersRequest,
  GetProjectEnrolmentsResponse,
  UpdateEnrolmentsRequest,
  GetTechnologiesResponse,
  AddTechnologyRequest,
  UpdateTechnologyRequest,
  RemoveUsersRequest,
} from './proserve-wb-projects-api';
import { getAccessToken } from '..';

const DEFAULT_PAGE_SIZE = '10';
const PROJECTS_API_NAME = 'ProjectsAPI';
const CACHE_CONTROL_HEADER = 'Cache-Control';
const AUTHORIZATION_HEADER = 'Authorization';

export type ProjectGroupAssignment = {
  projectId: string,
  groupId: string,
  roles: string[],
  groupName?: string,
};

export type GetProjectGroupsResponse = { assignments: ProjectGroupAssignment[] };

export async function fetchAllProjectPages(
  loadPage: (nextToken?: object) => Promise<GetProjectsResponse>
): Promise<GetProjectsResponse> {
  const projects = new Map<string, GetProjectsResponse['projects'][number]>();
  const assignments = new Map<string, NonNullable<GetProjectsResponse['assignments']>[number]>();
  const effectiveAccess = new Map<string, NonNullable<GetProjectsResponse['effectiveAccess']>[number]>();
  const enrolments = new Map<string, NonNullable<GetProjectsResponse['enrolments']>[number]>();
  const seenTokens = new Set<string>();
  let hasEffectiveAccess = false;
  let nextToken: object | undefined;
  do {
    // Pages depend on the previous continuation token.
    // eslint-disable-next-line no-await-in-loop
    const page = await loadPage(nextToken);
    page.projects.forEach(project => {
      if (project.projectId) {
        projects.set(project.projectId, project);
      }
    });
    page.assignments?.forEach(assignment => {
      if (assignment.projectId) {
        assignments.set(assignment.projectId, assignment);
      }
    });
    if (page.effectiveAccess !== undefined) {
      hasEffectiveAccess = true;
    }
    page.effectiveAccess?.forEach(access => effectiveAccess.set(access.projectId, access));
    page.enrolments?.forEach(enrolment => {
      if (enrolment.projectId) {
        enrolments.set(enrolment.projectId, enrolment);
      }
    });
    nextToken = page.nextToken ?? undefined;
    if (nextToken) {
      const key = JSON.stringify(nextToken);
      if (seenTokens.has(key)) {
        throw new Error('Repeated project page token');
      }
      seenTokens.add(key);
    }
  } while (nextToken);
  return {
    projects: [...projects.values()],
    assignments: [...assignments.values()],
    effectiveAccess: hasEffectiveAccess ? [...effectiveAccess.values()] : undefined,
    enrolments: [...enrolments.values()]
  };
}

/**
 *  Configure client SDK
 */
function prepareClient(): DefaultApi {
  const config = Amplify.getConfig();
  const basePath = config.API?.REST?.[PROJECTS_API_NAME]?.endpoint || '';
  const apiConfig = new Configuration({ basePath: basePath });
  return new DefaultApi(apiConfig);
}

export const projectsAPI = {

  getProjects: async(): Promise<GetProjectsResponse> => {
    const access = await getAccessToken();
    const api = prepareClient();
    return fetchAllProjectPages(nextToken => api.getProjects({
      authorization: `Bearer ${access}`,
      pageSize: Number(DEFAULT_PAGE_SIZE),
      // The generated type says object, but its query encoder must receive JSON text.
      nextToken: nextToken ? JSON.stringify(nextToken) as unknown as object : undefined
    }));
  },

  getProjectEnrolments: async(
    projectId: string,
    pageSize: string,
    nextToken?: string,
    status?: string
  ): Promise<GetProjectEnrolmentsResponse> => {
    const access = await getAccessToken();
    const api = prepareClient();
    return api.getProjectEnrolments({
      projectId,
      authorization: `Bearer ${access}`,
      pageSize: Number(pageSize || DEFAULT_PAGE_SIZE),
      nextToken: nextToken ? JSON.parse(nextToken) : undefined,
      status
    });
  },

  updateEnrolments: async(projectId: string, body: UpdateEnrolmentsRequest): Promise<object> => {
    const access = await getAccessToken();
    const api = prepareClient();
    return api.updateEnrolments({
      projectId,
      authorization: `Bearer ${access}`,
      updateEnrolmentsRequest: body
    });
  },

  getProjectAccounts: async (
    projectId: string,
    invalidateCache?: boolean
  ): Promise<GetProjectAccountsResponse> => {
    const access = await getAccessToken();

    const headers: { [key: string]: string } = {};
    headers[AUTHORIZATION_HEADER] = `Bearer ${access}`;
    if (invalidateCache) {
      headers[CACHE_CONTROL_HEADER] = 'max-age=0';
    }

    const api = prepareClient();
    return api.getProjectAccounts({
      authorization: `Bearer ${access}`,
      projectId: projectId
    },
    { headers: headers });
  },

  enrolUser: async(projectId: string, body: object): Promise<object> => {
    const access = await getAccessToken();
    const api = prepareClient();
    return api.enrolUser({
      authorization: `Bearer ${access}`,
      projectId: projectId,
      body
    });
  },

  onboardProjectAccount: async(projectId: string, body: OnBoardProjectAccountRequest): Promise<object> => {
    const access = await getAccessToken();
    const api = prepareClient();
    return api.addProjectAccount({
      authorization: `Bearer ${access}`,
      projectId: projectId,
      onBoardProjectAccountRequest: body
    });
  },

  reonboardProjectAccount: async(projectId: string, accountIds: string[]): Promise<object> => {
    const access = await getAccessToken();
    const api = prepareClient();
    return api.reonboardProjectAccount({
      authorization: `Bearer ${access}`,
      projectId: projectId,
      reonboardProjectAccountRequest: {
        accountIds: accountIds
      }
    });
  },

  getProjectUsers: async (
    projectId: string,
  ): Promise<GetProjectAssignmentsResponse> => {
    const access = await getAccessToken();
    const headers: { [key: string]: string } = {};
    headers[CACHE_CONTROL_HEADER] = 'max-age=0, no-cache';
    headers[AUTHORIZATION_HEADER] = `Bearer ${access}`;
    const api = prepareClient();
    return api.getProjectUsers({
      authorization: `Bearer ${access}`,
      projectId: projectId,
    },
    { headers: headers });
  },

  getProjectGroups: async(projectId: string): Promise<GetProjectGroupsResponse> => {
    const access = await getAccessToken();
    const basePath = Amplify.getConfig().API?.REST?.[PROJECTS_API_NAME]?.endpoint || '';
    const groupsUrl = `${basePath.replace(/\/$/u, '')}/projects/${encodeURIComponent(projectId)}/groups`;
    const response = await fetch(groupsUrl, {
      headers: {
        [AUTHORIZATION_HEADER]: `Bearer ${access}`,
        [CACHE_CONTROL_HEADER]: 'max-age=0, no-cache'
      }
    });
    if (!response.ok) {
      throw new Error(`Unable to fetch project groups (${response.status})`);
    }
    return response.json() as Promise<GetProjectGroupsResponse>;
  },

  getUserRoles: async(projectId: string, userId: string): Promise<GetUserRolesResponse> => {
    const access = await getAccessToken();
    const headers: { [key: string]: string } = {};
    headers[CACHE_CONTROL_HEADER] = 'max-age=0, no-cache';
    headers[AUTHORIZATION_HEADER] = `Bearer ${access}`;
    const api = prepareClient();
    return api.getUserRoles({
      authorization: `Bearer ${access}`,
      projectId: projectId,
      userId: userId
    },
    { headers: headers });
  },

  assignProjectUser: async(projectId: string, body: AssignUserRequest): Promise<object> => {
    const access = await getAccessToken();
    const api = prepareClient();
    return api.addProjectUser({
      authorization: `Bearer ${access}`,
      projectId: projectId,
      assignUserRequest: body
    });
  },

  removeProjectUser: async(projectId: string, userId: string): Promise<object> => {
    const access = await getAccessToken();
    const api = prepareClient();
    return api.removeProjectUser({
      authorization: `Bearer ${access}`,
      projectId: projectId,
      userId: userId
    });
  },

  removeProjectUsers: async(projectId: string, body: RemoveUsersRequest): Promise<object> => {
    const access = await getAccessToken();
    const api = prepareClient();
    return api.removeProjectUsers({
      authorization: `Bearer ${access}`,
      projectId: projectId,
      removeUsersRequest: body
    });
  },

  reassignProjectUsers: async(
    projectId: string,
    body: ReAssignUsersRequest
  ): Promise<object> => {
    const access = await getAccessToken();
    const api = prepareClient();
    return api.reAssignProjectUsers({
      authorization: `Bearer ${access}`,
      projectId: projectId,
      reAssignUsersRequest: body
    });
  },

  getTechnologies: async(
    projectId: string,
    pageSize: string,
    nextToken?: string): Promise<GetTechnologiesResponse> => {
    const access = await getAccessToken();
    const api = prepareClient();
    return api.getTechnologies({
      authorization: `Bearer ${access}`,
      projectId: projectId,
      pageSize: Number(pageSize),
      nextToken: nextToken ? JSON.parse(nextToken) : undefined,
    });
  },

  addTechnology: async(projectId: string, body: AddTechnologyRequest): Promise<object> => {
    const access = await getAccessToken();
    const api = prepareClient();
    return api.addTechnology({
      authorization: `Bearer ${access}`,
      projectId: projectId,
      addTechnologyRequest: body
    });
  },

  updateTechnology: async(
    projectId: string,
    techId: string,
    body: UpdateTechnologyRequest): Promise<object> => {
    const access = await getAccessToken();
    const api = prepareClient();
    return api.updateTechnology({
      authorization: `Bearer ${access}`,
      projectId: projectId,
      techId: techId,
      updateTechnologyRequest: body
    });
  },

  deleteTechnology: async(projectId: string, techId: string): Promise<object> => {
    const access = await getAccessToken();
    const api = prepareClient();
    return api.deleteTechnology({
      authorization: `Bearer ${access}`,
      projectId: projectId,
      techId: techId,
    });
  },

  activateProjectAccount: async(projectId: string, accountId: string): Promise<object> => {
    const access = await getAccessToken();
    const api = prepareClient();
    return api.updateProjectAccount({
      authorization: `Bearer ${access}`,
      projectId: projectId,
      accountId: accountId,
      updateProjectAccountRequest: {
        accountStatus: 'Active'
      }
    });
  },

  deactivateProjectAccount: async(projectId: string, accountId: string): Promise<object> => {
    const access = await getAccessToken();
    const api = prepareClient();
    return api.updateProjectAccount({
      authorization: `Bearer ${access}`,
      projectId: projectId,
      accountId: accountId,
      updateProjectAccountRequest: {
        accountStatus: 'Inactive'
      }
    });
  },

  createProject: async(
    name: string,
    description: string,
    isActive: boolean
  ) => {
    const access = await getAccessToken();
    const api = prepareClient();
    return api.createProject({
      authorization: `Bearer ${access}`,
      createProjectRequest: {
        name: name,
        description: description,
        isActive: isActive
      }
    });
  },

  getProject: async(
    projectId: string
  ) => {
    const access = await getAccessToken();
    const api = prepareClient();
    return api.getProject({
      authorization: `Bearer ${access}`,
      projectId
    });
  },

  updateProject: async(
    projectId: string,
    name: string,
    description: string,
    isActive: boolean
  ) => {
    const access = await getAccessToken();
    const api = prepareClient();
    return api.updateProject({
      authorization: `Bearer ${access}`,
      projectId,
      updateProjectRequest: {
        name: name,
        description: description,
        isActive: isActive
      }
    });
  },

};
