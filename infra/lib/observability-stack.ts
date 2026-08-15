import * as cdk from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as actions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as autoscaling from 'aws-cdk-lib/aws-autoscaling';
import { Construct } from 'constructs';
import { EnvConfig } from '../config/environments';

export interface ObservabilityStackProps extends cdk.StackProps {
  readonly config: EnvConfig;
  readonly database: rds.DatabaseInstance;
  readonly loadBalancer: elbv2.ApplicationLoadBalancer;
  readonly autoScalingGroup: autoscaling.AutoScalingGroup;
}

/**
 * Stack 4 of 4: Observability.
 *
 * Two classes of alarm are defined.
 *
 * Infrastructure alarms watch the servers: CPU, database connections, error
 * rate, response time.
 *
 * Programme alarms watch the health outcome the pilot exists to change. The
 * application publishes these metrics from the alert engine. If SMS delivery
 * fails or the EID result relay stalls, the programme alarm fires even when
 * every server is healthy. A green infrastructure dashboard does not mean that
 * infants are being linked to treatment.
 */
export class ObservabilityStack extends cdk.Stack {
  public readonly alarmTopic: sns.Topic;

  constructor(scope: Construct, id: string, props: ObservabilityStackProps) {
    super(scope, id, props);
    const { config, database, loadBalancer, autoScalingGroup } = props;
    const NS = 'MMDSA/Programme';

    this.alarmTopic = new sns.Topic(this, 'AlarmTopic', {
      topicName: `mmdsa-${config.envName}-alarms`,
      displayName: 'Mentor Mother application alarms',
    });
    this.alarmTopic.addSubscription(
      new subscriptions.EmailSubscription(config.alarmEmail),
    );

    new logs.LogGroup(this, 'AppLogGroup', {
      logGroupName: `/mmdsa/${config.envName}/application`,
      retention: config.logRetentionDays,
      removalPolicy:
        config.envName === 'pilot'
          ? cdk.RemovalPolicy.RETAIN
          : cdk.RemovalPolicy.DESTROY,
    });

    const attach = (alarm: cloudwatch.Alarm) => {
      alarm.addAlarmAction(new actions.SnsAction(this.alarmTopic));
      alarm.addOkAction(new actions.SnsAction(this.alarmTopic));
      return alarm;
    };

    // ---- Infrastructure alarms ------------------------------------------
    const infraAlarms = [
      attach(
        new cloudwatch.Alarm(this, 'HighApiLatency', {
          alarmDescription: 'Median API response time is above 2 seconds.',
          metric: loadBalancer.metrics.targetResponseTime({
            statistic: 'p50',
            period: cdk.Duration.minutes(5),
          }),
          threshold: 2,
          evaluationPeriods: 3,
          comparisonOperator:
            cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        }),
      ),
      attach(
        new cloudwatch.Alarm(this, 'ServerErrorRate', {
          alarmDescription: 'The API is returning 5xx responses.',
          metric: loadBalancer.metrics.httpCodeTarget(
            elbv2.HttpCodeTarget.TARGET_5XX_COUNT,
            { period: cdk.Duration.minutes(5) },
          ),
          threshold: 10,
          evaluationPeriods: 2,
          treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
          comparisonOperator:
            cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        }),
      ),
      attach(
        new cloudwatch.Alarm(this, 'NoHealthyTargets', {
          alarmDescription: 'No application server is passing the health check.',
          metric: loadBalancer.metrics.custom('HealthyHostCount', {
            statistic: 'Minimum',
            period: cdk.Duration.minutes(1),
          }),
          threshold: 1,
          evaluationPeriods: 2,
          comparisonOperator: cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
          treatMissingData: cloudwatch.TreatMissingData.BREACHING,
        }),
      ),
      attach(
        new cloudwatch.Alarm(this, 'DatabaseCpu', {
          alarmDescription: 'Database CPU is above 80 percent.',
          metric: database.metricCPUUtilization({
            period: cdk.Duration.minutes(5),
          }),
          threshold: 80,
          evaluationPeriods: 3,
          comparisonOperator:
            cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        }),
      ),
      attach(
        new cloudwatch.Alarm(this, 'DatabaseStorage', {
          alarmDescription: 'Free database storage is below 5 GB.',
          metric: database.metricFreeStorageSpace({
            period: cdk.Duration.minutes(15),
          }),
          threshold: 5 * 1024 * 1024 * 1024,
          evaluationPeriods: 2,
          comparisonOperator: cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
        }),
      ),
    ];

    // ---- Programme alarms ------------------------------------------------
    // The application emits these metrics. See backend/apps/common/metrics.py.
    const programmeMetric = (name: string, statistic = 'Sum') =>
      new cloudwatch.Metric({
        namespace: NS,
        metricName: name,
        statistic,
        period: cdk.Duration.hours(1),
        dimensionsMap: { Environment: config.envName },
      });

    const smsFailureRate = new cloudwatch.MathExpression({
      expression: 'IF(sent > 0, 100 * failed / sent, 0)',
      usingMetrics: {
        sent: programmeMetric('SmsDispatched'),
        failed: programmeMetric('SmsDeliveryFailed'),
      },
      label: 'SMS failure rate (percent)',
      period: cdk.Duration.hours(1),
    });

    const programmeAlarms = [
      attach(
        new cloudwatch.Alarm(this, 'SmsDeliveryFailureRate', {
          alarmDescription:
            'More than 10 percent of SMS messages failed. The alert relay is ' +
            'the causal mechanism of the intervention. Investigate at once.',
          metric: smsFailureRate,
          threshold: 10,
          evaluationPeriods: 2,
          comparisonOperator:
            cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
          treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
        }),
      ),
      attach(
        new cloudwatch.Alarm(this, 'AlertsBreachingSla', {
          alarmDescription:
            'Alerts have passed their acknowledgement deadline without action.',
          metric: programmeMetric('AlertsPastSla', 'Maximum'),
          threshold: 5,
          evaluationPeriods: 1,
          comparisonOperator:
            cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
          treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
        }),
      ),
      attach(
        new cloudwatch.Alarm(this, 'LocationVerificationBelowTarget', {
          alarmDescription:
            'Fewer than 85 percent of home visits carry a verified location ' +
            'record. This is the indicator target in the M and E framework.',
          metric: programmeMetric('VisitLocationVerifiedPercent', 'Average'),
          threshold: 85,
          evaluationPeriods: 24, // One full day of hourly readings.
          datapointsToAlarm: 20,
          comparisonOperator: cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
          treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
        }),
      ),
      attach(
        new cloudwatch.Alarm(this, 'OfflineSyncBacklog', {
          alarmDescription:
            'Visit records have stayed unsynchronised for more than 72 hours. ' +
            'Data is sitting on handsets and is at risk.',
          metric: programmeMetric('SyncRecordsOlderThan72h', 'Maximum'),
          threshold: 25,
          evaluationPeriods: 2,
          comparisonOperator:
            cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
          treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
        }),
      ),
      attach(
        new cloudwatch.Alarm(this, 'EidResultRelayStalled', {
          alarmDescription:
            'An EID result has been in the system for more than 48 hours ' +
            'without a supervisor acknowledgement.',
          metric: programmeMetric('EidResultsUnacknowledged48h', 'Maximum'),
          threshold: 1,
          evaluationPeriods: 1,
          comparisonOperator:
            cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
          treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
        }),
      ),
    ];

    // ---- Dashboard -------------------------------------------------------
    const dashboard = new cloudwatch.Dashboard(this, 'Dashboard', {
      dashboardName: `mmdsa-${config.envName}`,
    });

    dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown:
          `# Mentor Mother Digital Supervision — ${config.envName}\n` +
          'The programme row is the one that matters. Infrastructure health ' +
          'does not prove that the referral relay is working.',
        width: 24,
        height: 2,
      }),
    );

    dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'Referral relay — alerts raised, acknowledged, escalated',
        left: [
          programmeMetric('AlertsRaised'),
          programmeMetric('AlertsAcknowledged'),
          programmeMetric('AlertsEscalated'),
        ],
        width: 12,
      }),
      new cloudwatch.GraphWidget({
        title: 'SMS gateway — dispatched, delivered, failed, replies parsed',
        left: [
          programmeMetric('SmsDispatched'),
          programmeMetric('SmsDelivered'),
          programmeMetric('SmsDeliveryFailed'),
          programmeMetric('SmsReplyParsed'),
        ],
        width: 12,
      }),
    );

    dashboard.addWidgets(
      new cloudwatch.SingleValueWidget({
        title: 'Home visits with a verified location record (percent)',
        metrics: [programmeMetric('VisitLocationVerifiedPercent', 'Average')],
        width: 8,
      }),
      new cloudwatch.SingleValueWidget({
        title: 'Median hours from EID result entry to supervisor action',
        metrics: [programmeMetric('EidResultToActionHours', 'Average')],
        width: 8,
      }),
      new cloudwatch.SingleValueWidget({
        title: 'Records awaiting synchronisation',
        metrics: [programmeMetric('SyncRecordsPending', 'Maximum')],
        width: 8,
      }),
    );

    dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'API response time and request volume',
        left: [loadBalancer.metrics.targetResponseTime({ statistic: 'p50' }),
               loadBalancer.metrics.targetResponseTime({ statistic: 'p95' })],
        right: [loadBalancer.metrics.requestCount()],
        width: 12,
      }),
      new cloudwatch.GraphWidget({
        title: 'Database CPU and connections',
        left: [database.metricCPUUtilization()],
        right: [database.metricDatabaseConnections()],
        width: 12,
      }),
    );

    dashboard.addWidgets(
      new cloudwatch.AlarmStatusWidget({
        title: 'Programme alarms',
        alarms: programmeAlarms,
        width: 12,
      }),
      new cloudwatch.AlarmStatusWidget({
        title: 'Infrastructure alarms',
        alarms: infraAlarms,
        width: 12,
      }),
    );

    // Reference the ASG so a scaling event surfaces on the dashboard.
    dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'Application server count',
        left: [
          new cloudwatch.Metric({
            namespace: 'AWS/AutoScaling',
            metricName: 'GroupInServiceInstances',
            dimensionsMap: {
              AutoScalingGroupName: autoScalingGroup.autoScalingGroupName,
            },
            statistic: 'Average',
          }),
        ],
        width: 24,
      }),
    );

    new cdk.CfnOutput(this, 'AlarmTopicArn', { value: this.alarmTopic.topicArn });
    new cdk.CfnOutput(this, 'DashboardName', {
      value: `mmdsa-${config.envName}`,
    });
  }
}
