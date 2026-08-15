import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { Construct } from 'constructs';
import { EnvConfig } from '../config/environments';

export interface NetworkStackProps extends cdk.StackProps {
  readonly config: EnvConfig;
}

/**
 * Stack 1 of 4: Network.
 *
 * Creates the VPC and the network isolation boundary. The database and the
 * cache sit in isolated subnets. These subnets have no route to the internet,
 * inbound or outbound. Only the application security group can reach them.
 */
export class NetworkStack extends cdk.Stack {
  public readonly vpc: ec2.Vpc;
  public readonly albSecurityGroup: ec2.SecurityGroup;
  public readonly appSecurityGroup: ec2.SecurityGroup;
  public readonly dataSecurityGroup: ec2.SecurityGroup;
  public readonly cacheSecurityGroup: ec2.SecurityGroup;

  constructor(scope: Construct, id: string, props: NetworkStackProps) {
    super(scope, id, props);
    const { config } = props;

    this.vpc = new ec2.Vpc(this, 'Vpc', {
      vpcName: `mmdsa-${config.envName}`,
      ipAddresses: ec2.IpAddresses.cidr('10.40.0.0/16'),
      maxAzs: config.maxAzs,
      natGateways: config.natGateways,
      subnetConfiguration: [
        { name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
        { name: 'app', subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS, cidrMask: 22 },
        { name: 'data', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 24 },
      ],
      // VPC Flow Logs give a network-level audit trail. NDPA 2023 breach
      // investigation needs this record.
      flowLogs: {
        all: {
          trafficType: ec2.FlowLogTrafficType.ALL,
          destination: ec2.FlowLogDestination.toCloudWatchLogs(),
        },
      },
    });

    // ---- Security groups ------------------------------------------------
    // Traffic flows in one direction only: internet -> ALB -> app -> data.

    this.albSecurityGroup = new ec2.SecurityGroup(this, 'AlbSg', {
      vpc: this.vpc,
      description: 'Load balancer. Accepts HTTPS from the internet.',
      allowAllOutbound: false,
    });
    this.albSecurityGroup.addIngressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(443),
      'HTTPS from the internet',
    );

    this.appSecurityGroup = new ec2.SecurityGroup(this, 'AppSg', {
      vpc: this.vpc,
      description: 'Django application servers.',
      allowAllOutbound: true, // Needed for the Termii API and AWS service calls.
    });
    this.appSecurityGroup.addIngressRule(
      this.albSecurityGroup,
      ec2.Port.tcp(8000),
      'Gunicorn from the load balancer only',
    );
    this.albSecurityGroup.addEgressRule(
      this.appSecurityGroup,
      ec2.Port.tcp(8000),
      'To the application servers only',
    );

    this.dataSecurityGroup = new ec2.SecurityGroup(this, 'DataSg', {
      vpc: this.vpc,
      description: 'PostgreSQL. Reachable from the application only.',
      allowAllOutbound: false,
    });
    this.dataSecurityGroup.addIngressRule(
      this.appSecurityGroup,
      ec2.Port.tcp(5432),
      'PostgreSQL from the application servers only',
    );

    this.cacheSecurityGroup = new ec2.SecurityGroup(this, 'CacheSg', {
      vpc: this.vpc,
      description: 'Redis. Celery broker and result backend.',
      allowAllOutbound: false,
    });
    this.cacheSecurityGroup.addIngressRule(
      this.appSecurityGroup,
      ec2.Port.tcp(6379),
      'Redis from the application servers only',
    );

    // ---- VPC endpoints --------------------------------------------------
    // These endpoints keep AWS service traffic off the public internet and
    // cut the NAT gateway data charge.

    this.vpc.addGatewayEndpoint('S3Endpoint', {
      service: ec2.GatewayVpcEndpointAwsService.S3,
    });

    const interfaceEndpoints: Record<string, ec2.InterfaceVpcEndpointAwsService> = {
      SecretsManager: ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
      CloudWatchLogs: ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
      Ssm: ec2.InterfaceVpcEndpointAwsService.SSM,
      SsmMessages: ec2.InterfaceVpcEndpointAwsService.SSM_MESSAGES,
      Ec2Messages: ec2.InterfaceVpcEndpointAwsService.EC2_MESSAGES,
      Kms: ec2.InterfaceVpcEndpointAwsService.KMS,
    };

    for (const [name, service] of Object.entries(interfaceEndpoints)) {
      this.vpc.addInterfaceEndpoint(`${name}Endpoint`, {
        service,
        securityGroups: [this.appSecurityGroup],
        subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      });
    }

    new cdk.CfnOutput(this, 'VpcId', { value: this.vpc.vpcId });
  }
}
