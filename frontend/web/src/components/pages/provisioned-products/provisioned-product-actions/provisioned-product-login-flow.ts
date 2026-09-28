import {
  DcvConnectionDetails,
  getDcvConnectionDetails,
  isDcvGatewayConfigured,
  parseGatewayAddress,
} from '../../../../services/dcv-gateway-api';
import { WORKBENCH_CONNECTION_TYPE } from '../../workbenches/workbenches.static';
import { LoginRequest, LoginResponse } from './provisioned-product-login-types/interface';
import { ProvisionedProductLoginType } from './provisioned-product-login-types/login-type';

interface ProvisionedProductLoginFlowRequest {
  connectionOption: string,
  loginType: ProvisionedProductLoginType,
  loginRequest: LoginRequest,
  authorizeDirectConnection: () => Promise<void>,
}

function isDcvConnectionOption(connectionOption: string | null): boolean {
  return connectionOption === WORKBENCH_CONNECTION_TYPE.DcvBrowser ||
    connectionOption === WORKBENCH_CONNECTION_TYPE.DcvFile;
}

export function canLoginWithoutDirectAddress(connectionOption: string | null): boolean {
  return isDcvGatewayConfigured() && isDcvConnectionOption(connectionOption);
}

export async function performProvisionedProductLogin({
  connectionOption,
  loginType,
  loginRequest,
  authorizeDirectConnection,
}: ProvisionedProductLoginFlowRequest): Promise<LoginResponse> {
  let connectionDetails: DcvConnectionDetails | undefined;
  if (canLoginWithoutDirectAddress(connectionOption)) {
    connectionDetails = await getDcvConnectionDetails(
      loginRequest.provisionedProduct.projectId,
      loginRequest.provisionedProduct.provisionedProductId,
    );
  }

  const gatewayAddress = connectionDetails?.mode === 'gateway' ?
    parseGatewayAddress(connectionDetails.gatewayUrl) : undefined;

  if (connectionDetails?.mode !== 'gateway') {
    await authorizeDirectConnection();
  }

  return loginType.doLogin({
    ...loginRequest,
    connectAddress: gatewayAddress?.host ?? loginRequest.connectAddress,
  }, connectionDetails?.mode === 'gateway' ? {
    sessionId: connectionDetails.sessionId,
    authToken: connectionDetails.token,
    portOverride: gatewayAddress?.port,
  } : {});
}
