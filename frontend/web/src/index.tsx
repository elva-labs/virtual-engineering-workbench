// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
//
// Loads the deployment's runtime configuration (runtime-config.ts), then the app (main.tsx), whose
// modules read it when they load.
import { loadRuntimeConfig } from './runtime-config';

loadRuntimeConfig().
  then(() => import('./main')).
  catch((error: unknown) => {
    document.body.textContent = `The portal could not start: ${String(error)}`;
    throw error;
  });
