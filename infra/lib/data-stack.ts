import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as elasticache from 'aws-cdk-lib/aws-elasticache';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';
import { EnvConfig } from '../config/environments';

export interface DataStackProps extends cdk.StackProps {
  readonly config: EnvConfig;
  readonly vpc: ec2.Vpc;
  readonly dataSecurityGroup: ec2.SecurityGroup;
  readonly cacheSecurityGroup: ec2.SecurityGroup;
}

/**
 * Stack 2 of 4: Data.
 *
 * Holds every store that contains patient data: the PostgreSQL database, the
 * Redis cache, the document bucket and the audit log bucket.
 *
 * All four stores use a customer-managed KMS key. A customer-managed key gives
 * an auditable key policy and supports key rotation on a schedule, which the
 * NDPA 2023 compliance annex commits to.
 */
export class DataStack extends cdk.Stack {
  public readonly encryptionKey: kms.Key;
  public readonly database: rds.DatabaseInstance;
  public readonly databaseSecret: secretsmanager.ISecret;
  public readonly redisEndpoint: string;
  public readonly redisPort: string;
  public readonly documentBucket: s3.Bucket;
  public readonly auditBucket: s3.Bucket;
  public readonly appSecrets: secretsmanager.Secret;

  constructor(scope: Construct, id: string, props: DataStackProps) {
    super(scope, id, props);
    const { config, vpc, dataSecurityGroup, cacheSecurityGroup } = props;
    const isPilot = config.envName === 'pilot';

    // ---- Encryption key -------------------------------------------------
    this.encryptionKey = new kms.Key(this, 'DataKey', {
      alias: `alias/mmdsa-${config.envName}-data`,
      description: 'Encrypts all Mentor Mother application data at rest.',
      enableKeyRotation: true,
      rotationPeriod: cdk.Duration.days(365),
      removalPolicy: isPilot ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
    });

    // ---- PostgreSQL 16 --------------------------------------------------
    const parameterGroup = new rds.ParameterGroup(this, 'PgParams', {
      engine: rds.DatabaseInstanceEngine.postgres({
        version: rds.PostgresEngineVersion.VER_16_4,
      }),
      description: 'Mentor Mother application. Logging and audit settings.',
      parameters: {
        // Reject any connection that does not use TLS.
        'rds.force_ssl': '1',
        // Log every data definition change. Needed for the audit trail.
        log_statement: 'ddl',
        // Log any statement slower than 1 second.
        log_min_duration_statement: '1000',
        log_connections: '1',
        log_disconnections: '1',
        // pgcrypto supports column-level encryption of direct identifiers.
        shared_preload_libraries: 'pg_stat_statements',
      },
    });

    this.database = new rds.DatabaseInstance(this, 'Postgres', {
      engine: rds.DatabaseInstanceEngine.postgres({
        version: rds.PostgresEngineVersion.VER_16_4,
      }),
      instanceType: new ec2.InstanceType(config.dbInstanceClass),
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [dataSecurityGroup],
      databaseName: 'mmdsa',
      credentials: rds.Credentials.fromGeneratedSecret('mmdsa_app', {
        secretName: `mmdsa/${config.envName}/db-credentials`,
        encryptionKey: this.encryptionKey,
      }),
      allocatedStorage: config.dbAllocatedStorageGb,
      maxAllocatedStorage: config.dbAllocatedStorageGb * 4,
      storageType: rds.StorageType.GP3,
      multiAz: config.dbMultiAz,
      storageEncrypted: true,
      storageEncryptionKey: this.encryptionKey,
      backupRetention: cdk.Duration.days(config.dbBackupRetentionDays),
      preferredBackupWindow: '01:00-02:00', // 02:00-03:00 West Africa Time.
      preferredMaintenanceWindow: 'sun:02:30-sun:03:30',
      deletionProtection: config.dbDeletionProtection,
      removalPolicy: isPilot ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
      parameterGroup,
      enablePerformanceInsights: true,
      performanceInsightEncryptionKey: this.encryptionKey,
      cloudwatchLogsExports: ['postgresql', 'upgrade'],
      autoMinorVersionUpgrade: true,
      // Point-in-time recovery is implied by backupRetention > 0.
    });
    this.databaseSecret = this.database.secret!;

    // ---- Redis (Celery broker) ------------------------------------------
    const cacheSubnets = new elasticache.CfnSubnetGroup(this, 'CacheSubnets', {
      description: 'Isolated subnets for the Celery broker.',
      subnetIds: vpc.selectSubnets({ subnetType: ec2.SubnetType.PRIVATE_ISOLATED })
        .subnetIds,
      cacheSubnetGroupName: `mmdsa-${config.envName}-cache`,
    });

    const redis = new elasticache.CfnReplicationGroup(this, 'Redis', {
      replicationGroupDescription: 'Celery broker and result backend.',
      engine: 'redis',
      engineVersion: '7.1',
      cacheNodeType: config.redisNodeType,
      numCacheClusters: config.redisNumNodes,
      automaticFailoverEnabled: config.redisNumNodes > 1,
      multiAzEnabled: config.redisNumNodes > 1,
      cacheSubnetGroupName: cacheSubnets.cacheSubnetGroupName,
      securityGroupIds: [cacheSecurityGroup.securityGroupId],
      atRestEncryptionEnabled: true,
      kmsKeyId: this.encryptionKey.keyId,
      transitEncryptionEnabled: true,
      snapshotRetentionLimit: isPilot ? 7 : 1,
      port: 6379,
    });
    redis.node.addDependency(cacheSubnets);

    this.redisEndpoint = redis.attrPrimaryEndPointAddress;
    this.redisPort = redis.attrPrimaryEndPointPort;

    // ---- Object storage --------------------------------------------------
    // Documents: signed consent forms and exported reports.
    this.documentBucket = new s3.Bucket(this, 'DocumentBucket', {
      bucketName: `mmdsa-${config.envName}-documents-${this.account}`,
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: this.encryptionKey,
      enforceSSL: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      versioned: true,
      removalPolicy: isPilot ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: !isPilot,
      lifecycleRules: [
        {
          id: 'expire-old-versions',
          noncurrentVersionExpiration: cdk.Duration.days(90),
        },
      ],
      serverAccessLogsPrefix: 'access-logs/',
    });

    // Audit log archive. Object Lock in compliance mode makes the record
    // tamper-proof, which the NDPA 2023 audit schedule requires.
    this.auditBucket = new s3.Bucket(this, 'AuditBucket', {
      bucketName: `mmdsa-${config.envName}-audit-${this.account}`,
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: this.encryptionKey,
      enforceSSL: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      versioned: true,
      objectLockEnabled: isPilot,
      objectLockDefaultRetention: isPilot
        ? s3.ObjectLockRetention.compliance(cdk.Duration.days(2555)) // 7 years.
        : undefined,
      removalPolicy: isPilot ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: !isPilot,
    });

    // ---- Application secrets --------------------------------------------
    // The Termii API key is placed here as an empty value. Set the real value
    // through the console or the CLI after deployment. Never commit a key.
    this.appSecrets = new secretsmanager.Secret(this, 'AppSecrets', {
      secretName: `mmdsa/${config.envName}/app`,
      description: 'Django secret key, Termii API credentials, JWT signing key.',
      encryptionKey: this.encryptionKey,
      generateSecretString: {
        secretStringTemplate: JSON.stringify({
          TERMII_API_KEY: 'REPLACE_AFTER_DEPLOY',
          TERMII_SENDER_ID: 'REPLACE_AFTER_DEPLOY',
          FIELD_ENCRYPTION_KEY: 'REPLACE_AFTER_DEPLOY',
        }),
        generateStringKey: 'DJANGO_SECRET_KEY',
        excludePunctuation: false,
        passwordLength: 64,
      },
    });

    new cdk.CfnOutput(this, 'DbEndpoint', {
      value: this.database.dbInstanceEndpointAddress,
    });
    new cdk.CfnOutput(this, 'RedisEndpoint', { value: this.redisEndpoint });
  }
}
