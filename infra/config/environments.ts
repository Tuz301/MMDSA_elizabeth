/**
 * Environment configuration for the Mentor Mother Digital Supervision Application.
 *
 * The AWS Africa (Cape Town) region (af-south-1) is used for all environments.
 * This keeps personal health information inside Africa and reduces the data
 * sovereignty risk under the Nigeria Data Protection Act 2023.
 *
 * NOTE: af-south-1 is an opt-in region. Enable it in the account before you
 * bootstrap the CDK. Some services are not available in af-south-1. See
 * lib/README-region-constraints.md.
 */

export type EnvName = 'dev' | 'pilot';

export interface EnvConfig {
  readonly envName: EnvName;
  readonly account?: string;
  readonly region: string;

  /** Number of Availability Zones. Multi-AZ is required for the pilot. */
  readonly maxAzs: number;
  readonly natGateways: number;

  /** RDS PostgreSQL settings. */
  readonly dbInstanceClass: string;
  readonly dbAllocatedStorageGb: number;
  readonly dbMultiAz: boolean;
  readonly dbBackupRetentionDays: number;
  readonly dbDeletionProtection: boolean;

  /** ElastiCache Redis settings. Redis is the Celery broker. */
  readonly redisNodeType: string;
  readonly redisNumNodes: number;

  /** Application Auto Scaling group size. */
  readonly appMinCapacity: number;
  readonly appMaxCapacity: number;
  readonly appInstanceType: string;

  /** Retention period for CloudWatch logs, in days. */
  readonly logRetentionDays: number;

  /** Email address that receives operational alarms. */
  readonly alarmEmail: string;

  /** Fully qualified domain name of the API. Leave empty to use the ALB name. */
  readonly apiDomainName?: string;
  readonly certificateArn?: string;
}

const BASE = {
  region: 'af-south-1',
  // A placeholder on purpose: alarms must go to a monitored inbox, and that
  // address is deployment configuration. Override at synth time with
  //   --context alarmEmail=ops@example.org
  // The AppStack refuses to stay quiet if this placeholder reaches a pilot
  // synth. An alarm nobody receives is the paper register all over again.
  alarmEmail: 'platform-alerts@datapharm.example',
};

export const ENVIRONMENTS: Record<EnvName, EnvConfig> = {
  /**
   * Development environment. Single AZ, no deletion protection, smallest
   * instance sizes. This environment must never hold real patient data.
   */
  dev: {
    ...BASE,
    envName: 'dev',
    maxAzs: 2,
    natGateways: 1,
    dbInstanceClass: 't4g.small',
    dbAllocatedStorageGb: 20,
    dbMultiAz: false,
    dbBackupRetentionDays: 1,
    dbDeletionProtection: false,
    redisNodeType: 'cache.t4g.micro',
    redisNumNodes: 1,
    appMinCapacity: 1,
    appMaxCapacity: 2,
    appInstanceType: 't4g.small',
    logRetentionDays: 30,
  },

  /**
   * Pilot environment. This environment holds real patient data. Multi-AZ,
   * deletion protection on, 35-day backup retention.
   */
  pilot: {
    ...BASE,
    envName: 'pilot',
    maxAzs: 2,
    natGateways: 2,
    dbInstanceClass: 't4g.medium',
    dbAllocatedStorageGb: 50,
    dbMultiAz: true,
    dbBackupRetentionDays: 35,
    dbDeletionProtection: true,
    redisNodeType: 'cache.t4g.small',
    redisNumNodes: 2,
    // Two instances minimum: one instance normally serving everything is a
    // single point of failure the multi-AZ database cannot compensate for.
    appMinCapacity: 2,
    appMaxCapacity: 4,
    appInstanceType: 't4g.medium',
    logRetentionDays: 365,
  },
};

export function getEnv(name: string | undefined): EnvConfig {
  const key = (name ?? 'dev') as EnvName;
  const cfg = ENVIRONMENTS[key];
  if (!cfg) {
    throw new Error(
      `Unknown environment "${name}". Valid values are: ${Object.keys(ENVIRONMENTS).join(', ')}`,
    );
  }
  return cfg;
}
