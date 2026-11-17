// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
//
// Loads the deployment's runtime configuration (runtime-config.ts), then the app (main.tsx), whose
// modules read it when they load.
import { loadRuntimeConfig } from './runtime-config';
import { reloadOnceForStaleBuild } from './stale-build';

loadRuntimeConfig().
  then(() => import('./main').catch((error: unknown) => {
    // A page from before the last deployment asks for chunks that no longer exist (stale-build.ts).
    if (reloadOnceForStaleBuild()) {
      // The page is going away: neither start nor show the error meanwhile.
      return new Promise<never>(() => { /* never settles */ });
    }
    throw error;
  })).
  catch((error: unknown) => {
    document.body.textContent = `The portal could not start: ${String(error)}`;
    throw error;
  });
