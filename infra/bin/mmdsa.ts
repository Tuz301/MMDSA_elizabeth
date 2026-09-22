#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { getEnv } from '../config/environments';
import { NetworkStack } from '../lib/network-stack';
import { DataStack } from '../lib/data-stack';
import { AppStack } from '../lib/app-stack';
import { ObservabilityStack } from '../lib/observability-stack';

/**
 * Mentor Mother Digital Supervision Application — infrastructure entrypoint.
 *
 * Deploy one environment at a time:
 *   npx cdk deploy --all --context env=dev
 *   npx cdk deploy --all --context env=pilot
 */
const app = new cdk.App();

const envName = app.node.tryGetContext('env') ?? process.env.MMDSA_ENV ?? 'dev';
let config = getEnv(envName);

// The alarm address is deployment configuration, not code. Supply it with
//   --context alarmEmail=ops@example.org
// An alarm that notifies a placeholder notifies nobody; the AppStack warns
// loudly when the placeholder survives into a pilot synth.
const alarmEmail = app.node.tryGetContext('alarmEmail');
if (alarmEmail) {
  config = { ...config, alarmEmail };
}

const env: cdk.Environment = {
  account: config.account ?? process.env.CDK_DEFAULT_ACCOUNT,
  region: config.region,
};

const prefix = `Mmdsa-${config.envName}`;

const network = new NetworkStack(app, `${prefix}-Network`, { env, config });

const data = new DataStack(app, `${prefix}-Data`, {
  env,
  config,
  vpc: network.vpc,
  dataSecurityGroup: network.dataSecurityGroup,
  cacheSecurityGroup: network.cacheSecurityGroup,
});
data.addStackDependency(network);

const appStack = new AppStack(app, `${prefix}-App`, {
  env,
  config,
  vpc: network.vpc,
  albSecurityGroup: network.albSecurityGroup,
  appSecurityGroup: network.appSecurityGroup,
  encryptionKey: data.encryptionKey,
  database: data.database,
  databaseSecret: data.databaseSecret,
  appSecrets: data.appSecrets,
  documentBucket: data.documentBucket,
  auditBucket: data.auditBucket,
  redisEndpoint: data.redisEndpoint,
  redisPort: data.redisPort,
});
appStack.addStackDependency(data);

const observability = new ObservabilityStack(app, `${prefix}-Observability`, {
  env,
  config,
  database: data.database,
  loadBalancer: appStack.loadBalancer,
  autoScalingGroup: appStack.autoScalingGroup,
});
observability.addStackDependency(appStack);

// Tags applied to every resource. Cost allocation and the NDPA 2023 data
// classification both depend on these.
cdk.Tags.of(app).add('Project', 'MentorMotherDigitalSupervision');
cdk.Tags.of(app).add('Environment', config.envName);
cdk.Tags.of(app).add('Owner', 'DataPharm-Technologies');
cdk.Tags.of(app).add('Partner', 'IHVN');
cdk.Tags.of(app).add('Funder', 'CDC-PEPFAR-Innovation');
cdk.Tags.of(app).add('DataClassification', 'PHI-Restricted');
cdk.Tags.of(app).add('ManagedBy', 'AWS-CDK');

app.synth();
