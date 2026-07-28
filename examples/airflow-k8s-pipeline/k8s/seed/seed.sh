#!/bin/bash
set -e

LOCALSTACK_ENDPOINT="http://localstack.default.svc.cluster.local:4566"
BUCKET="ids-pipeline"
TOPIC_NAME="ids-fdr-events"
QUEUE_NAME="ids-fdr-queue"
SNS_REGION="us-west-2"
SQS_REGION="us-west-2"

echo "=== Creating S3 bucket ==="
aws --endpoint-url=$LOCALSTACK_ENDPOINT s3 mb s3://$BUCKET --region $SNS_REGION || echo "Bucket already exists"

echo "=== Creating SNS topic ==="
TOPIC_ARN=$(aws --endpoint-url=$LOCALSTACK_ENDPOINT sns create-topic --name $TOPIC_NAME --region $SNS_REGION --query 'TopicArn' --output text)
echo "Topic ARN: $TOPIC_ARN"

echo "=== Creating SQS queue ==="
QUEUE_URL=$(aws --endpoint-url=$LOCALSTACK_ENDPOINT sqs create-queue --queue-name $QUEUE_NAME --region $SQS_REGION --query 'QueueUrl' --output text)
echo "Queue URL: $QUEUE_URL"

QUEUE_ARN=$(aws --endpoint-url=$LOCALSTACK_ENDPOINT sqs get-queue-attributes --queue-url $QUEUE_URL --attribute-names QueueArn --region $SQS_REGION --query 'Attributes.QueueArn' --output text)
echo "Queue ARN: $QUEUE_ARN"

echo "=== Setting SQS policy for SNS ==="
POLICY='{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": "*",
    "Action": "sqs:SendMessage",
    "Resource": "'$QUEUE_ARN'",
    "Condition": {
      "ArnEquals": {
        "aws:SourceArn": "'$TOPIC_ARN'"
      }
    }
  }]
}'
aws --endpoint-url=$LOCALSTACK_ENDPOINT sqs set-queue-attributes --queue-url $QUEUE_URL --attributes Policy="$POLICY" --region $SQS_REGION

echo "=== Subscribing SQS to SNS ==="
SUB_ARN=$(aws --endpoint-url=$LOCALSTACK_ENDPOINT sns subscribe --topic-arn $TOPIC_ARN --protocol sqs --notification-endpoint $QUEUE_ARN --region $SNS_REGION --query 'SubscriptionArn' --output text)
echo "Subscription ARN: $SUB_ARN"

echo "=== Configuring S3 bucket notification ==="
NOTIFICATION_CONFIG='{
  "TopicConfigurations": [{
    "TopicArn": "'$TOPIC_ARN'",
    "Events": ["s3:ObjectCreated:*"],
    "Filter": {
      "Key": {
        "FilterRules": [{
          "Name": "suffix",
          "Value": ".VIC"
        }]
      }
    }
  }]
}'
aws --endpoint-url=$LOCALSTACK_ENDPOINT s3api put-bucket-notification-configuration --bucket $BUCKET --notification-configuration "$NOTIFICATION_CONFIG" --region $SNS_REGION

echo "=== Uploading sample FDR files ==="
# Upload left eye
aws --endpoint-url=$LOCALSTACK_ENDPOINT s3 cp /data/NLM_1835_0829848458_777FDR_N0874924NCAM00230_0A02LLJ01.VIC s3://$BUCKET/global_cache/1835/ncam/

# Upload right eye
aws --endpoint-url=$LOCALSTACK_ENDPOINT s3 cp /data/NRM_1835_0829848458_777FDR_N0874924NCAM00230_0A02LLJ01.VIC s3://$BUCKET/global_cache/1835/ncam/

echo "=== Seed complete ==="
echo "Bucket: s3://$BUCKET"
echo "Topic: $TOPIC_ARN"
echo "Queue: $QUEUE_URL"
