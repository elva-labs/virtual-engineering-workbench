function getEnvironmentName(): string {
  if (!import.meta.env.REACT_APP_ENVIRONMENT) {
    throw new Error('REACT_APP_ENVIRONMENT environment variable is not set.');
  }
  return import.meta.env.REACT_APP_ENVIRONMENT;
}

const dcvGatewayUrl: string | undefined = import.meta.env.DCV_GATEWAY_URL;

const appConfig = {
  Environment: getEnvironmentName(),
  DcvGatewayUrl: dcvGatewayUrl,
};

export { appConfig as AppConfig };
