"""
IDS Pipeline Listener
Consumes MinIO bucket-notification events from a RabbitMQ queue, matches keys
against PROCESS_MAP, buffers stereo pairs, triggers Airflow DAGs via REST API.
"""
import json
import os
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime
from urllib.parse import unquote_plus

import pika
import requests

# Config
AMQP_URL = os.getenv("AMQP_URL", "amqp://guest:guest@rabbitmq.tig-airflow.svc.cluster.local:5672/%2F")
AMQP_EXCHANGE = os.getenv("AMQP_EXCHANGE", "ids-events")
AMQP_QUEUE = os.getenv("AMQP_QUEUE", "ids-fdr-queue")
AIRFLOW_API_URL = os.getenv("AIRFLOW_API_URL", "http://airflow-webserver.tig-airflow.svc.cluster.local:8080/api/v1")
AIRFLOW_USERNAME = os.getenv("AIRFLOW_USERNAME", "admin")
AIRFLOW_PASSWORD = os.getenv("AIRFLOW_PASSWORD", "admin")
RECONNECT_DELAY = int(os.getenv("RECONNECT_DELAY", "5"))
STEREO_PAIR_TIMEOUT = int(os.getenv("STEREO_PAIR_TIMEOUT", "300"))  # 5 minutes

# Process map (subset for NCAM FDR → terrain mesh)
PROCESS_MAP = {
    "ids_terrain_ncam": [
        r"global_cache/\d+/ncam/N[LR]M_\d+_\d+_\d+FDR_[A-Z0-9_]+\.VIC"
    ]
}

# Stereo pair buffer: {frame_id: {"left": key, "right": key, "timestamp": datetime}}
stereo_buffer = defaultdict(lambda: {"left": None, "right": None, "timestamp": None})


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
    """Match S3 key against PROCESS_MAP regex patterns"""
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


def process_event(body):
    """Process one MinIO bucket-notification event (S3-compatible record format)"""
    try:
        s3_event = json.loads(body)

        # MinIO records URL-encode the object key
        for record in s3_event.get("Records", []):
            bucket = record["s3"]["bucket"]["name"]
            key = unquote_plus(record["s3"]["object"]["key"])

            print(f"Received: s3://{bucket}/{key}")

            dag_id = match_process(key)
            if not dag_id:
                print(f"  No process match for {key}, skipping")
                continue

            print(f"  Matched process: {dag_id}")

            frame_id = extract_frame_id(key)
            eye = extract_eye(key)

            if not frame_id or not eye:
                print(f"  Failed to extract frame_id/eye from {key}, skipping")
                continue

            print(f"  Frame: {frame_id}, Eye: {eye}")

            pair = stereo_buffer[frame_id]
            if not pair["timestamp"]:
                pair["timestamp"] = datetime.utcnow()
            pair[eye] = key

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
                    stereo_buffer.pop(frame_id)
                    return True
            else:
                missing = "right" if pair["left"] else "left"
                print(f"  Waiting for {missing} eye (timeout in {STEREO_PAIR_TIMEOUT}s)")

        return True

    except Exception as e:
        print(f"✗ Error processing event: {e}")
        return False


def on_message(channel, method, properties, body):
    print(f"[{datetime.utcnow().isoformat()}] Received message (delivery_tag={method.delivery_tag})")
    cleanup_expired_pairs()
    if process_event(body):
        channel.basic_ack(delivery_tag=method.delivery_tag)
        print("  Message acked")
    else:
        # Requeue once; a poison message goes back to the queue tail
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
        print("  Message nacked (requeued)")


def consume():
    """Open a connection, declare the queue topology, and block consuming"""
    connection = pika.BlockingConnection(pika.URLParameters(AMQP_URL))
    channel = connection.channel()
    # Idempotent: matches the definitions pre-loaded into RabbitMQ
    channel.exchange_declare(exchange=AMQP_EXCHANGE, exchange_type="fanout", durable=True)
    channel.queue_declare(queue=AMQP_QUEUE, durable=True)
    channel.queue_bind(exchange=AMQP_EXCHANGE, queue=AMQP_QUEUE)
    channel.basic_qos(prefetch_count=10)
    channel.basic_consume(queue=AMQP_QUEUE, on_message_callback=on_message)
    print(f"Consuming from queue {AMQP_QUEUE} (exchange {AMQP_EXCHANGE})")
    try:
        channel.start_consuming()
    finally:
        connection.close()


def main():
    print("=== IDS Pipeline Listener ===")
    print(f"AMQP: {AMQP_URL}")
    print(f"Exchange: {AMQP_EXCHANGE}")
    print(f"Queue: {AMQP_QUEUE}")
    print(f"Airflow API: {AIRFLOW_API_URL}")
    print(f"Stereo pair timeout: {STEREO_PAIR_TIMEOUT}s")
    print()

    while True:
        try:
            consume()
        except pika.exceptions.AMQPError as e:
            print(f"✗ AMQP error: {e}; reconnecting in {RECONNECT_DELAY}s")
            time.sleep(RECONNECT_DELAY)
        except Exception as e:
            print(f"✗ Error in main loop: {e}; reconnecting in {RECONNECT_DELAY}s")
            time.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    main()
