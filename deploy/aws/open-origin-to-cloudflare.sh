#!/usr/bin/env bash
# Make the dim0 EC2 origin reachable from Cloudflare Workers (and the internet)
# so the workers.dev reverse proxy can fetch it.
#
# Does two things, idempotently:
#   1. Ensures the instance with public IP $ORIGIN_IP (52.220.166.153, the
#      stable Elastic IP) is Running — start it if stopped. The instance can
#      OOM during research and stop; without this the proxy returns 522.
#   2. Ensures the instance's security group allows inbound HTTP/80 and HTTPS/443
#      from 0.0.0.0/0. If the SG only allowed the operator's home IP, Cloudflare
#      edge IPs were blocked (also 522).
#
# Usage:  bash deploy/aws/open-origin-to-cloudflare.sh [region]
# Default region: ap-southeast-1. Needs AWS credentials configured (aws CLI).
# Safe to re-run.

set -euo pipefail

REGION="${1:-${AWS_DEFAULT_REGION:-ap-southeast-1}}"
# Elastic IP attached to the dim0 instance — stable across stop/start (an OOM
# restart previously released the dynamic public IP and broke the proxy).
ORIGIN_IP="52.220.166.153"

echo "==> region=$REGION  origin=$ORIGIN_IP"

# --- find instance by public IP ---
INSTANCE_ID=$(aws ec2 describe-instances \
  --region "$REGION" \
  --filters "Name=ip-address,Values=$ORIGIN_IP" \
           "Name=instance-state-name,Values=pending,running,shutting-down,stopping,stopped" \
  --query 'Reservations[].Instances[].InstanceId' --output text | head -1)

if [ -z "$INSTANCE_ID" ]; then
  echo "ERROR: no instance with public IP $ORIGIN_IP in $REGION." >&2
  echo "Check the IP, region, and that the instance still exists." >&2
  exit 1
fi
echo "==> instance: $INSTANCE_ID"

STATE=$(aws ec2 describe-instances --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].State.Name' --output text)
echo "==> state: $STATE"

if [ "$STATE" != "running" ]; then
  echo "==> starting instance (currently $STATE)..."
  aws ec2 start-instances --region "$REGION" --instance-ids "$INSTANCE_ID" >/dev/null
  echo "==> waiting for running state..."
  aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"
  STATE=running
  echo "==> state: $STATE"
  # Public IP can change on stop/start for non-EIP instances; warn.
  NEW_IP=$(aws ec2 describe-instances --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
  echo "==> public IP now: $NEW_IP (if this differs from $ORIGIN_IP, update"
  echo "    deploy/aws/cloudflare-worker/src/worker.js ORIGIN_HOST nip.io host)"
fi

# --- collect security groups attached to the instance ---
SG_IDS=$(aws ec2 describe-instances --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].SecurityGroups[].GroupId' --output text)
echo "==> security groups: $SG_IDS"

for SG in $SG_IDS; do
  for RULE in "80 tcp 0.0.0.0/0 HTTP" "443 tcp 0.0.0.0/0 HTTPS"; do
    PORT=$(echo "$RULE" | awk '{print $1}')
    PROTO=$(echo "$RULE" | awk '{print $2}')
    CIDR=$(echo "$RULE" | awk '{print $3}')
    NAME=$(echo "$RULE" | awk '{print $4}')
    # Is the rule already present (any description)?
    EXISTS=$(aws ec2 describe-security-groups --region "$REGION" --group-ids "$SG" \
      --query "SecurityGroups[0].IpPermissions[?FromPort==\`$PORT\` && ToPort==\`$PORT\` && IpProtocol=='$PROTO'].IpRanges[?CidrIp=='$CIDR'].CidrIp" \
      --output text 2>/dev/null || true)
    if [ -n "$EXISTS" ]; then
      echo "==> $SG: $NAME $PORT/$PROTO from $CIDR already open"
    else
      echo "==> $SG: opening $NAME $PORT/$PROTO from $CIDR"
      aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG" \
        --ip-permissions "IpProtocol=$PROTO,FromPort=$PORT,ToPort=$PORT,IpRanges=[{CidrIp=$CIDR}]" >/dev/null
      echo "    done"
    fi
  done
done

echo "==> all done. The Cloudflare proxy at https://dim0-proxy.dim0-thang.workers.dev"
echo "    should now reach the origin (200). Re-test:"
printf '    curl -s -o /dev/null -w "%%{http_code}\\n" https://dim0-proxy.dim0-thang.workers.dev/api/integration/health\n'