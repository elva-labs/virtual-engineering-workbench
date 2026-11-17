import {
  Context,
  APIGatewayProxyEvent,
  APIGatewayProxyResult
} from 'aws-lambda';
import { S3Client, GetObjectCommand, GetObjectCommandInput } from '@aws-sdk/client-s3';
import { Logger } from '@aws-lambda-powertools/logger';
import { injectLambdaContext } from '@aws-lambda-powertools/logger/middleware';

import middy from '@middy/core';
import { cognitoAuth } from '../helpers/alb-cognito-auth';

const ssmClient = new S3Client();

const logger = new Logger();

const APP_BUCKET_NAME = process.env.APP_BUCKET_NAME || '';
const USER_POOL_ID = process.env.USER_POOL_ID || '';
const USER_POOL_APP_ID = process.env.USER_POOL_APP_ID || '';
const USER_POOL_DOMAIN = process.env.USER_POOL_DOMAIN || '';

const DOMAIN_NAME = process.env.DOMAIN_NAME || '';
const DEFAULT_REGION = process.env.DEFAULT_REGION || '';
const LOG_LEVEL = process.env.POWERTOOLS_LOG_LEVEL || '';
const COOKIE_EXPIRATION_DAYS = 2;
const INDEX_FILE_NAME = 'index.html';

const retry: <TResponse>(
  func: () => Promise<TResponse | undefined>,
  retryCondition: () => boolean,
  retries?: number,
) => Promise<TResponse | undefined> = async (func, retryCondition, retries = 1) => {
  for (let i = 0; i <= retries; i++) {
    try {
      return await func();
    } catch (e) {
      logger.error(JSON.stringify(e));
      if (!retryCondition()) {
        break;
      }
    }
  }
  return undefined;
};

// Vite writes the build's JavaScript and CSS under assets/ with a content hash in the name, so a
// file there never changes: browsers may keep it. Everything else (index.html, the SPA fallback,
// aws-exports.json, favicons) keeps its name across builds and is revalidated on every load,
// otherwise a cached index.html keeps pointing at the previous build's chunks, which the
// deployment removes (`aws s3 sync --delete` in deploy.sh), and the portal fails with "Failed to
// fetch dynamically imported module".
const HASHED_ASSETS_PREFIX = 'assets/';
const CACHE_CONTROL = 'Cache-Control';
const CACHE_HASHED = 'public, max-age=31536000, immutable';
const CACHE_REVALIDATE = 'no-cache';
// A 404 is not cached, so the next deployment's file of that name is fetched.
const CACHE_NONE = 'no-store';

type MultiValueHeaders = { [header: string]: (string | number | boolean)[] };

function cacheHeaders(cacheControl: string): MultiValueHeaders {
  const headers: MultiValueHeaders = {};
  headers[CACHE_CONTROL] = [cacheControl];
  return headers;
}

function cacheControlFor(hashed: boolean): string {
  return hashed ? CACHE_HASHED : CACHE_REVALIDATE;
}

/*
  Handles the fetching of the VEW static content replicating CloudFront behaviour:
  * "/" path tries to fetch index.html
  * if requested resource is not found in S3, tries to fetch index.html
  * except under assets/: a missing hashed file is a previous build's, not a route, and answering
    it with index.html (text/html, 200) would hide that from the browser
*/
export const lambdaHandler = async (event: APIGatewayProxyEvent, context: Context): Promise<APIGatewayProxyResult> => {

  const bucketName = getBucketName(event.path);
  const path = getPath(event.path);
  const hashed = path.startsWith(HASHED_ASSETS_PREFIX);

  const s3Params: GetObjectCommandInput = {
    Bucket: bucketName,
    Key: path,
  };

  const object = await retry(() => ssmClient.send(new GetObjectCommand(s3Params)), () => {
    if (!hashed && s3Params.Key !== INDEX_FILE_NAME) {
      s3Params.Key = INDEX_FILE_NAME;
      return true;
    }
    return false;
  });

  if (object === undefined) {
    return {
      statusCode: 404,
      multiValueHeaders: cacheHeaders(CACHE_NONE),
      body: '',
    };
  }

  let body = '';
  if (object?.Body !== undefined) {
    const arr = await object.Body.transformToByteArray();
    body = Buffer.from(arr).toString('base64');
  }

  const multiValueHeaders = cacheHeaders(cacheControlFor(hashed));
  if (object?.ContentType !== undefined) {
    multiValueHeaders['Content-Type'] = [object.ContentType];
  }

  const response = {
    statusCode: 200,
    isBase64Encoded: true,
    multiValueHeaders,
    body,
  };

  return response;
};

function getBucketName(path: string) {
  return APP_BUCKET_NAME;
}

function getPath(originalPath: string): string {
  let path = originalPath;
  if (new Set(['/', '']).has(path)) {
    path = INDEX_FILE_NAME;
  }
  if (path.length > 0 && path[0] === '/') {
    path = path.substring(1);
  }
  return path;
}


export const handler = middy(lambdaHandler).
  use(injectLambdaContext(logger, { logEvent: true })).
  use(cognitoAuth({
    dnsName: DOMAIN_NAME,
    region: DEFAULT_REGION,
    logLevel: LOG_LEVEL,
    cookieExpirationDays: COOKIE_EXPIRATION_DAYS,
    userPoolId: USER_POOL_ID,
    userPoolClientId: USER_POOL_APP_ID,
    userPoolDomain: USER_POOL_DOMAIN,
    logger,
  }));
