"""
IDS Pipeline Listener
Polls SQS for S3 ObjectCreated events, matches keys against process_map.json,
buffers stereo pairs, triggers Airflow DAGs via REST API.
"""
import json
import os
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
import boto3
import requests

# Config
LOCALSTACK_ENDPOINT = os.getenv("LOCALSTACK_ENDPOINT", "http://localstack.default.svc.cluster.local:4566")
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL", "http://localstack.default.svc.cluster.local:4566/000000000000/ids-fdr-queue")
AIRFLOW_API_URL = os.getenv("AIRFLOW_API_URL", "http://airflow-webserver.default.svc.cluster.local:8080/api/v1")
AIRFLOW_USERNAME = os.getenv("AIRFLOW_USERNAME", "admin")
AIRFLOW_PASSWORD = os.getenv("AIRFLOW_PASSWORD", "admin")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "5"))
STEREO_PAIR_TIMEOUT = int(os.getenv("STEREO_PAIR_TIMEOUT", "300"))  # 5 minutes

# Process map (subset for NCAM FDR → terrain mesh)
PROCESS_MAP = {
    "ids_terrain_ncam": [
        r"global_cache/\d+/ncam/N[LR]M_\d+_\d+_\d+FDR_[A-Z0-9_]+\.VIC"
    ]
}

# Stereo pair buffer: {frame_id: {"left": key, "right": key, "timestamp": datetime}}
stereo_buffer = defaultdict(lambda: {"left": None, "right": None, "timestamp": None})

# Initialize boto3 client
sqs = boto3.client(
    "sqs",
    endpoint_url=LOCALSTACK_ENDPOINT,
    region_name="us-west-2",
    aws_access_key_id="test",
    aws_secret_access_key="test",
)


def extract_frame_id(s3_key):
    """
    Extract frame id from NCAM FDR filename.
    Example: NLM_1835_0829848458_777FDR_N0874924NCAM00230_0A02LLJ01.VIC
             → 0829848458_777FDR_N0874924NCAM00230
    """
    match = re.search(r"N[LR]M_\d+_(\d+_\d+FDR_[A-Z0-9]+)", s3_key)
    if match:
        return match.group(1)
    return None


def extract_eye(s3_key):
    """Extract left/right eye from filename (NLM = left, NRM = right)"""
    if "/NLM_" in s3_key:
        return "left"
    elif "/NRM_" in s3_key:
        return "right"
    return None


def match_process(s3_key):
    """Match S3 key against process_map.json regex patterns"""
    for process_name, patterns in PROCESS_MAP.items():
        for pattern in patterns:
            if re.search(pattern, s3_key):
                return process_name
    return None


def trigger_dag(dag_id, run_id, conf):
    """Trigger Airflow DAG via REST API"""
    url = f"{AIRFLOW_API_URL}/dags/{dag_id}/dagRuns"
    payload = {
        "dag_run_id": run_id,
        "conf": conf,
    }
    auth = (AIRFLOW_USERNAME, AIRFLOW_PASSWORD)
    
    try:
        resp = requests.post(url, json=payload, auth=auth, timeout=10)
        resp.raise_for_status()
        print(f"✓ Triggered DAG {dag_id}, run_id={run_id}")
        return True
    except requests.RequestException as e:
        print(f"✗ Failed to trigger DAG {dag_id}: {e}")
        return False


def cleanup_expired_pairs():
    """Remove stereo pairs older than STEREO_PAIR_TIMEOUT"""
    now = datetime.utcnow()
    expired = []
    for frame_id, pair in stereo_buffer.items():
        if pair["timestamp"] and (now - pair["timestamp"]).total_seconds() > STEREO_PAIR_TIMEOUT:
            expired.append(frame_id)
    
    for frame_id in expired:
        pair = stereo_buffer.pop(frame_id)
        print(f"⚠ Stereo pair timeout: {frame_id}, left={pair['left']}, right={pair['right']}")


def process_message(message):
    """Process SQS message (S3 ObjectCreated event)"""
    try:
        body = json.loads(message["Body"])
        
        # SNS wraps S3 event
        if "Message" in body:
            s3_event = json.loads(body["Message"])
        else:
            s3_event = body
        
        # Extract S3 bucket + key
        for record in s3_event.get("Records", []):
            bucket = record["s3"]["bucket"]["name"]
            key = record["s3"]["object"]["key"]
            
            print(f"Received: s3://{bucket}/{key}")
            
            # Match against process_map
            dag_id = match_process(key)
            if not dag_id:
                print(f"  No process match for {key}, skipping")
                continue
            
            print(f"  Matched process: {dag_id}")
            
            # Extract frame_id + eye
            frame_id = extract_frame_id(key)
            eye = extract_eye(key)
            
            if not frame_id or not eye:
                print(f"  Failed to extract frame_id/eye from {key}, skipping")
                continue
            
            print(f"  Frame: {frame_id}, Eye: {eye}")
            
            # Buffer stereo pair
            pair = stereo_buffer[frame_id]
            if not pair["timestamp"]:
                pair["timestamp"] = datetime.utcnow()
            pair[eye] = key
            
            # Check if both L+R ready
            if pair["left"] and pair["right"]:
                run_id = f"manual__{frame_id}__{uuid.uuid4().hex[:8]}"
                conf = {
                    "bucket": bucket,
                    "left_key": pair["left"],
                    "right_key": pair["right"],
                    "run_id": run_id,
                }
                
                print(f"  Stereo pair complete: {frame_id}")
                print(f"    Left: {pair['left']}")
                print(f"    Right: {pair['right']}")
                
                if trigger_dag(dag_id, run_id, conf):
                    # Remove from buffer on success
                    stereo_buffer.pop(frame_id)
                    return True
            else:
                missing = "right" if pair["left"] else "left"
                print(f"  Waiting for {missing} eye (timeout in {STEREO_PAIR_TIMEOUT}s)")
        
        return True
    
    except Exception as e:
        print(f"✗ Error processing message: {e}")
        return False


def main():
    """Main polling loop"""
    print("=== IDS Pipeline Listener ===")
    print(f"LocalStack: {LOCALSTACK_ENDPOINT}")
    print(f"SQS Queue: {SQS_QUEUE_URL}")
    print(f"Airflow API: {AIRFLOW_API_URL}")
    print(f"Poll interval: {POLL_INTERVAL}s")
    print(f"Stereo pair timeout: {STEREO_PAIR_TIMEOUT}s")
    print()
    
    while True:
        try:
            # Cleanup expired pairs
            cleanup_expired_pairs()
            
            # Poll SQS (long-poll 20s)
            response = sqs.receive_message(
                QueueUrl=SQS_QUEUE_URL,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20,
                AttributeNames=["All"],
            )
            
            messages = response.get("Messages", [])
            if not messages:
                print(f"[{datetime.utcnow().isoformat()}] No messages")
                continue
            
            print(f"[{datetime.utcnow().isoformat()}] Received {len(messages)} message(s)")
            
            for message in messages:
                if process_message(message):
                    # Delete message on success
                    sqs.delete_message(
                        QueueUrl=SQS_QUEUE_URL,
                        ReceiptHandle=message["ReceiptHandle"],
                    )
                    print("  Message deleted from queue")
        
        except Exception as e:
            print(f"✗ Error in main loop: {e}")
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
