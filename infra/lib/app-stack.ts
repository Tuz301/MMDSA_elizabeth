import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as autoscaling from 'aws-cdk-lib/aws-autoscaling';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as certificatemanager from 'aws-cdk-lib/aws-certificatemanager';
import { Construct } from 'constructs';
import { EnvConfig } from '../config/environments';

export interface AppStackProps extends cdk.StackProps {
  readonly config: EnvConfig;
  readonly vpc: ec2.Vpc;
  readonly albSecurityGroup: ec2.SecurityGroup;
  readonly appSecurityGroup: ec2.SecurityGroup;
  readonly encryptionKey: kms.Key;
  readonly database: rds.DatabaseInstance;
  readonly databaseSecret: secretsmanager.ISecret;
  readonly appSecrets: secretsmanager.Secret;
  readonly documentBucket: s3.Bucket;
  readonly auditBucket: s3.Bucket;
  readonly redisEndpoint: string;
  readonly redisPort: string;
}

/**
 * Stack 3 of 4: Application.
 *
 * Runs the Django API on an EC2 Auto Scaling group behind an Application Load
 * Balancer, and creates the Cognito user pool that issues the JWTs.
 *
 * DEVIATION FROM ANNEX B: Annex B lists AWS API Gateway in front of the
 * application. This stack uses an Application Load Balancer with AWS WAF
 * instead. The reason is cost and latency: API Gateway charges per request on
 * top of the load balancer, and it adds a second hop. The WAF web ACL supplies
 * the rate limiting and the request filtering that Annex B expects from API
 * Gateway. Confirm this change with the technical reviewer before submission.
 */
export class AppStack extends cdk.Stack {
  public readonly loadBalancer: elbv2.ApplicationLoadBalancer;
  public readonly userPool: cognito.UserPool;
  public readonly autoScalingGroup: autoscaling.AutoScalingGroup;

  constructor(scope: Construct, id: string, props: AppStackProps) {
    super(scope, id, props);
    const {
      config, vpc, albSecurityGroup, appSecurityGroup, encryptionKey,
      database, databaseSecret, appSecrets, documentBucket, auditBucket,
      redisEndpoint, redisPort,
    } = props;
    const isPilot = config.envName === 'pilot';

    // ---- Identity --------------------------------------------------------
    // Every user of the system is a health worker. Self sign-up is disabled.
    // A programme administrator creates each account.
    this.userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: `mmdsa-${config.envName}`,
      selfSignUpEnabled: false,
      signInAliases: { username: true, email: true, phone: true },
      standardAttributes: {
        email: { required: false, mutable: true },
        phoneNumber: { required: true, mutable: true },
        fullname: { required: true, mutable: true },
      },
      customAttributes: {
        // The Django role name. Mapped to the five-tier access matrix.
        role: new cognito.StringAttribute({ minLen: 3, maxLen: 40, mutable: true }),
        facility_code: new cognito.StringAttribute({ maxLen: 20, mutable: true }),
        lga_code: new cognito.StringAttribute({ maxLen: 20, mutable: true }),
      },
      passwordPolicy: {
        minLength: 12,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
        tempPasswordValidity: cdk.Duration.days(3),
      },
      // Every account that can read patient data must use a second factor.
      mfa: cognito.Mfa.REQUIRED,
      mfaSecondFactor: { sms: true, otp: true },
      accountRecovery: cognito.AccountRecovery.PHONE_AND_EMAIL,
      featurePlan: cognito.FeaturePlan.PLUS,
      standardThreatProtectionMode:
        cognito.StandardThreatProtectionMode.FULL_FUNCTION,
      removalPolicy: isPilot ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
    });

    // Five groups. These map one to one onto the Django role choices.
    const groups = [
      ['mentor-mother', 'Logs home visits. Sees own assigned clients only.', 50],
      ['facility-supervisor', 'Enters clinical data. Sees own facility.', 40],
      ['lga-coordinator', 'Sees all facilities in the assigned LGA.', 30],
      ['state-manager', 'Sees all LGAs in the assigned state.', 20],
      ['system-admin', 'Full access. Administers users and configuration.', 10],
    ] as const;

    for (const [name, description, precedence] of groups) {
      new cognito.CfnUserPoolGroup(this, `Group${name}`, {
        userPoolId: this.userPool.userPoolId,
        groupName: name,
        description,
        precedence,
      });
    }

    const mobileClient = this.userPool.addClient('MobileClient', {
      userPoolClientName: 'mmdsa-mobile',
      authFlows: { userSrp: true, custom: false, userPassword: false },
      generateSecret: false, // Public client. A mobile app cannot hold a secret.
      accessTokenValidity: cdk.Duration.hours(1),
      idTokenValidity: cdk.Duration.hours(1),
      refreshTokenValidity: cdk.Duration.days(30),
      preventUserExistenceErrors: true,
      enableTokenRevocation: true,
    });

    const webClient = this.userPool.addClient('WebClient', {
      userPoolClientName: 'mmdsa-web-dashboard',
      authFlows: { userSrp: true },
      generateSecret: false,
      accessTokenValidity: cdk.Duration.minutes(30),
      idTokenValidity: cdk.Duration.minutes(30),
      refreshTokenValidity: cdk.Duration.days(1),
      preventUserExistenceErrors: true,
      enableTokenRevocation: true,
    });

    // ---- Instance role ---------------------------------------------------
    const instanceRole = new iam.Role(this, 'AppInstanceRole', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      description: 'Django application servers.',
      managedPolicies: [
        // Session Manager replaces SSH. No bastion host, no open port 22.
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('CloudWatchAgentServerPolicy'),
      ],
    });

    // Permissions are written as identity policies on the role, not as
    // resource policies on the target. A resource policy in the Data stack
    // would have to name this role, and the Application stack already names
    // the Data stack. That pair of references is a deployment cycle, and
    // CloudFormation rejects it.
    //
    // Identity policies are enough here. The KMS key keeps the default root
    // account statement, which lets an IAM policy grant use of the key. S3 and
    // Secrets Manager both allow same-account access through an identity
    // policy alone.
    instanceRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ReadDeploymentSecrets',
        actions: [
          'secretsmanager:GetSecretValue',
          'secretsmanager:DescribeSecret',
        ],
        resources: [databaseSecret.secretArn, appSecrets.secretArn],
      }),
    );

    instanceRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'UseDataEncryptionKey',
        actions: [
          'kms:Decrypt',
          'kms:Encrypt',
          'kms:GenerateDataKey',
          'kms:DescribeKey',
        ],
        resources: [encryptionKey.keyArn],
      }),
    );

    instanceRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ReadWriteDocuments',
        actions: [
          's3:GetObject',
          's3:PutObject',
          's3:DeleteObject',
          's3:ListBucket',
        ],
        resources: [documentBucket.bucketArn, `${documentBucket.bucketArn}/*`],
      }),
    );

    // Write only. The application must never be able to alter or remove an
    // audit record it has already written.
    instanceRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AppendAuditRecords',
        actions: ['s3:PutObject'],
        resources: [`${auditBucket.bucketArn}/*`],
      }),
    );

    instanceRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CognitoUserAdministration',
        actions: [
          'cognito-idp:AdminCreateUser',
          'cognito-idp:AdminDisableUser',
          'cognito-idp:AdminEnableUser',
          'cognito-idp:AdminGetUser',
          'cognito-idp:AdminAddUserToGroup',
          'cognito-idp:AdminRemoveUserFromGroup',
          'cognito-idp:AdminUpdateUserAttributes',
          'cognito-idp:ListUsers',
        ],
        resources: [this.userPool.userPoolArn],
      }),
    );

    // ---- Compute ---------------------------------------------------------
    const userData = ec2.UserData.forLinux();
    userData.addCommands(
      'set -euo pipefail',
      'dnf update -y',
      'dnf install -y docker amazon-cloudwatch-agent',
      'systemctl enable --now docker',
      // The deployment pipeline replaces this placeholder with a real pull and
      // run of the application image. See infra/README.md, "Deployment".
      'echo "mmdsa bootstrap complete" > /var/log/mmdsa-bootstrap.log',
    );

    this.autoScalingGroup = new autoscaling.AutoScalingGroup(this, 'AppAsg', {
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      instanceType: new ec2.InstanceType(config.appInstanceType),
      machineImage: ec2.MachineImage.latestAmazonLinux2023({
        cpuType: ec2.AmazonLinuxCpuType.ARM_64, // Graviton. Lower cost per request.
      }),
      securityGroup: appSecurityGroup,
      role: instanceRole,
      userData,
      minCapacity: config.appMinCapacity,
      maxCapacity: config.appMaxCapacity,
      healthChecks: autoscaling.HealthChecks.ec2({
        gracePeriod: cdk.Duration.minutes(5),
      }),
      blockDevices: [
        {
          deviceName: '/dev/xvda',
          volume: autoscaling.BlockDeviceVolume.ebs(30, {
            encrypted: true,
            volumeType: autoscaling.EbsDeviceVolumeType.GP3,
          }),
        },
      ],
      updatePolicy: autoscaling.UpdatePolicy.rollingUpdate({
        maxBatchSize: 1,
        minInstancesInService: config.appMinCapacity,
        pauseTime: cdk.Duration.minutes(5),
      }),
    });

    this.autoScalingGroup.scaleOnCpuUtilization('CpuScaling', {
      targetUtilizationPercent: 65,
      cooldown: cdk.Duration.minutes(3),
    });

    // ---- Load balancer ---------------------------------------------------
    this.loadBalancer = new elbv2.ApplicationLoadBalancer(this, 'Alb', {
      vpc,
      internetFacing: true,
      securityGroup: albSecurityGroup,
      deletionProtection: isPilot,
      dropInvalidHeaderFields: true,
    });

    const targetGroup = new elbv2.ApplicationTargetGroup(this, 'AppTargets', {
      vpc,
      port: 8000,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targets: [this.autoScalingGroup],
      deregistrationDelay: cdk.Duration.seconds(30),
      healthCheck: {
        path: '/api/v1/health/',
        healthyThresholdCount: 2,
        unhealthyThresholdCount: 3,
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
      },
    });

    if (config.certificateArn) {
      const certificate = certificatemanager.Certificate.fromCertificateArn(
        this, 'ApiCertificate', config.certificateArn,
      );
      this.loadBalancer.addListener('Https', {
        port: 443,
        protocol: elbv2.ApplicationProtocol.HTTPS,
        certificates: [certificate],
        sslPolicy: elbv2.SslPolicy.TLS13_RES,
        defaultTargetGroups: [targetGroup],
      });
      this.loadBalancer.addListener('HttpRedirect', {
        port: 80,
        protocol: elbv2.ApplicationProtocol.HTTP,
        defaultAction: elbv2.ListenerAction.redirect({
          protocol: 'HTTPS', port: '443', permanent: true,
        }),
      });
    } else {
      // Development only. A certificate is mandatory before any real patient
      // data enters the system.
      this.loadBalancer.addListener('HttpDev', {
        port: 80,
        protocol: elbv2.ApplicationProtocol.HTTP,
        defaultTargetGroups: [targetGroup],
      });
      cdk.Annotations.of(this).addWarning(
        'No certificate ARN is set. The listener is plain HTTP. ' +
        'Do not load patient data into this environment.',
      );
    }

    // ---- Web application firewall ---------------------------------------
    const webAcl = new wafv2.CfnWebACL(this, 'WebAcl', {
      name: `mmdsa-${config.envName}`,
      scope: 'REGIONAL',
      defaultAction: { allow: {} },
      visibilityConfig: {
        cloudWatchMetricsEnabled: true,
        metricName: `mmdsa-${config.envName}-waf`,
        sampledRequestsEnabled: true,
      },
      rules: [
        {
          name: 'RateLimitPerIp',
          priority: 0,
          action: { block: {} },
          statement: {
            rateBasedStatement: { limit: 2000, aggregateKeyType: 'IP' },
          },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: 'RateLimitPerIp',
            sampledRequestsEnabled: true,
          },
        },
        {
          name: 'AWSManagedCommonRules',
          priority: 1,
          overrideAction: { none: {} },
          statement: {
            managedRuleGroupStatement: {
              vendorName: 'AWS',
              name: 'AWSManagedRulesCommonRuleSet',
            },
          },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: 'CommonRules',
            sampledRequestsEnabled: true,
          },
        },
        {
          name: 'AWSManagedSqlInjection',
          priority: 2,
          overrideAction: { none: {} },
          statement: {
            managedRuleGroupStatement: {
              vendorName: 'AWS',
              name: 'AWSManagedRulesSQLiRuleSet',
            },
          },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: 'SqlInjection',
            sampledRequestsEnabled: true,
          },
        },
      ],
    });

    new wafv2.CfnWebACLAssociation(this, 'WebAclAssociation', {
      resourceArn: this.loadBalancer.loadBalancerArn,
      webAclArn: webAcl.attrArn,
    });

    // ---- Outputs consumed by the Django settings module ------------------
    new cdk.CfnOutput(this, 'ApiUrl', {
      value: `https://${config.apiDomainName ?? this.loadBalancer.loadBalancerDnsName}`,
    });
    new cdk.CfnOutput(this, 'UserPoolId', { value: this.userPool.userPoolId });
    new cdk.CfnOutput(this, 'MobileClientId', { value: mobileClient.userPoolClientId });
    new cdk.CfnOutput(this, 'WebClientId', { value: webClient.userPoolClientId });
    new cdk.CfnOutput(this, 'DbSecretArn', { value: databaseSecret.secretArn });
    new cdk.CfnOutput(this, 'RedisUrl', {
      value: `rediss://${redisEndpoint}:${redisPort}/0`,
    });
    new cdk.CfnOutput(this, 'DbInstanceId', { value: database.instanceIdentifier });
  }
}
