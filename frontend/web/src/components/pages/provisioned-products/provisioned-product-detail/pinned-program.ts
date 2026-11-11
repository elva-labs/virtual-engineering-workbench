import { useState } from 'react';

type Pinned = {
  provisionedProductId?: string,
  programId?: string,
};

/**
 * The program a detail page was opened under, kept while the page shows the same provisioned product.
 *
 * Switching program sets the new program before the router leaves the page: React Router runs a
 * navigation in a transition, so the program change renders first. Reading the live program would
 * fetch the old workbench under the new program, and the backend answers "Provisioned product with
 * id: ... does not exist."
 */
export function usePinnedProgramId(provisionedProductId?: string, programId?: string): string | undefined {
  const [pinned, setPinned] = useState<Pinned>({ provisionedProductId, programId });

  const samePage = pinned.provisionedProductId === provisionedProductId;
  if (samePage && pinned.programId) {
    return pinned.programId;
  }
  if (!samePage || pinned.programId !== programId) {
    setPinned({ provisionedProductId, programId });
  }
  return programId;
}
