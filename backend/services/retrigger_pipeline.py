"""
Retrigger (re-ingestion) pipeline service - GE Workflow Implementation.

Matches the exact workflow from GE production scripts:
  Step 1: Full table scan → build invoice index → resolve PKs
  Step 2: Query by PK → delete all SK variants (METADATA + ATTACHMENT#)
  Step 3: S3 copy-to-self → trigger Lambda reprocessing

Supports pause/resume via progress tracking in the job document.
"""

import time
from datetime import datetime, timezone
from decimal import Decimal

from config import make_source_aws_session
from tools.retrigger_tools import (
    get_retrigger_job,
    update_retrigger_job,
    append_job_log,
    update_job_step,
)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def run_retrigger_pipeline(job_id: str, invoice_numbers: list[str], cfg: dict, resume_from_step: str = None) -> None:
    """
    Main pipeline: fetch PKs → delete records → reingest files.
    Matches GE workflow exactly with pause/resume support.
    """
    job = get_retrigger_job(job_id)
    if not job:
        print(f"[Retrigger] Job {job_id} not found")
        return
    
    # Check if paused
    if job.get("status") == "paused":
        append_job_log(job_id, "info", "Job is paused. Use resume endpoint to continue.")
        return
    
    update_retrigger_job(job_id, {"status": "running"})
    append_job_log(job_id, "info", f"Starting retrigger pipeline for {len(invoice_numbers)} invoice(s) - Folder: {job.get('folder_name', 'N/A')}")

    try:
        # Determine starting step
        start_step = resume_from_step or "fetch_pks"
        
        # Step 1: Fetch partition keys (full table scan)
        if start_step == "fetch_pks":
            _step_fetch_pks_ge_workflow(job_id, invoice_numbers, cfg)
            
            # Check if paused after this step
            job = get_retrigger_job(job_id)
            if job.get("status") == "paused":
                return

        # Step 2: Delete records (query by PK)
        if start_step in ["fetch_pks", "delete_records"]:
            _step_delete_records_ge_workflow(job_id, cfg)
            
            # Check if paused after this step
            job = get_retrigger_job(job_id)
            if job.get("status") == "paused":
                return

        # Step 3: Re-ingest files (S3 copy-to-self)
        if start_step in ["fetch_pks", "delete_records", "reingest_files"]:
            _step_reingest_files_ge_workflow(job_id, cfg)

        # Mark job as completed
        update_retrigger_job(job_id, {"status": "completed", "paused_at_step": None})
        append_job_log(job_id, "info", "Pipeline completed successfully")

    except Exception as exc:
        error_msg = str(exc)
        update_retrigger_job(job_id, {"status": "failed", "error": error_msg})
        append_job_log(job_id, "error", f"Pipeline failed: {error_msg}")
        print(f"[Retrigger] Job {job_id} failed: {exc}")


def run_fetch_records(job_id: str, invoice_numbers: list[str], cfg: dict) -> None:
    """
    On-demand fetch of processed records from DynamoDB after re-ingestion.
    Updates the job's 'records' field with fetched data.
    """
    append_job_log(job_id, "info", f"Fetching records for {len(invoice_numbers)} invoice(s)")
    update_job_step(job_id, "fetch_records", "running")

    try:
        session = make_source_aws_session()
        dynamodb = session.resource("dynamodb", region_name=cfg["aws_region"])
        table = dynamodb.Table(cfg["dynamodb_table"])

        records = []
        for inv_num in invoice_numbers:
            resp = table.scan(
                FilterExpression="invoice_number = :inv",
                ExpressionAttributeValues={":inv": inv_num},
            )
            for item in resp.get("Items", []):
                records.append(_from_dynamo(item))

        update_retrigger_job(job_id, {"records": records})
        update_job_step(job_id, "fetch_records", "completed", {"count": len(records)})
        append_job_log(job_id, "info", f"Fetched {len(records)} record(s)")

    except Exception as exc:
        error_msg = str(exc)
        update_job_step(job_id, "fetch_records", "failed", {"error": error_msg})
        append_job_log(job_id, "error", f"Fetch records failed: {error_msg}")
        print(f"[Retrigger] Fetch records failed for job {job_id}: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline steps - GE Workflow Implementation
# ─────────────────────────────────────────────────────────────────────────────

def _step_fetch_pks_ge_workflow(job_id: str, invoice_numbers: list[str], cfg: dict) -> dict:
    """
    Step 1: Scan entire DynamoDB table and build in-memory index by invoice_number.
    For each invoice_number, keep ONLY the most recent PK (based on created_at_iso).
    Deduplicate PKs across all invoices (if multiple invoices share same PK, keep it once).
    
    Returns: {invoice_number: {"pk": ..., "message_id": ..., "created_at": ...}}
    """
    append_job_log(job_id, "info", "Step 1/3: Fetching partition keys from DynamoDB (full table scan)")
    update_job_step(job_id, "fetch_pks", "running")

    try:
        session = make_source_aws_session()
        dynamodb = session.resource("dynamodb", region_name=cfg["aws_region"])
        table = dynamodb.Table(cfg["dynamodb_table"])

        # Build in-memory index by scanning entire table
        # Structure: {invoice_number: [{"pk": ..., "created_at_iso": ..., "sk": ...}, ...]}
        invoice_index = {}
        scanned_count = 0
        scan_kwargs = {}
        
        append_job_log(job_id, "info", "Starting full table scan...")
        
        while True:
            resp = table.scan(**scan_kwargs)
            items = resp.get("Items", [])
            
            # Add items to index - collect ALL records with timestamps
            for item in items:
                inv_num = item.get("invoice_number")
                pk = item.get("pk")
                sk = item.get("sk")
                created_at = item.get("created_at_iso") or item.get("created_at") or ""
                
                # Only process METADATA or ATTACHMENT records with valid pk
                if inv_num and pk and (sk == "METADATA" or (sk and sk.startswith("ATTACHMENT#"))):
                    if inv_num not in invoice_index:
                        invoice_index[inv_num] = {}
                    
                    # Group by PK - track all SKs and timestamp for this PK
                    if pk not in invoice_index[inv_num]:
                        invoice_index[inv_num][pk] = {
                            "pk": pk,
                            "created_at_iso": created_at,
                            "sk_variants": []
                        }
                    
                    # Collect SK variants
                    if sk not in invoice_index[inv_num][pk]["sk_variants"]:
                        invoice_index[inv_num][pk]["sk_variants"].append(sk)
            
            scanned_count += len(items)
            
            # Live log update
            if scanned_count % 1000 == 0:
                append_job_log(job_id, "info", f"Scanning DynamoDB - {scanned_count} items scanned...")
                update_job_step(job_id, "fetch_pks", "running", {"scanned": scanned_count})
            
            # Check for more pages
            if "LastEvaluatedKey" not in resp:
                break
            scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        
        append_job_log(job_id, "info", f"Scan complete. Total items scanned: {scanned_count}")
        
        # Step 2: For each invoice, find the MOST RECENT PK (latest created_at_iso)
        invoice_to_latest_pk = {}  # {invoice_number: {"pk": ..., "created_at": ..., "sk_variants": [...]}}
        
        for inv_num, pks_dict in invoice_index.items():
            if not pks_dict:
                continue
            
            # Find the PK with the latest timestamp
            latest_pk_record = max(
                pks_dict.values(),
                key=lambda x: x.get("created_at_iso", "")
            )
            
            invoice_to_latest_pk[inv_num] = latest_pk_record
        
        # Step 3: Resolve requested invoice numbers and deduplicate PKs
        pk_records = []
        unique_pks_seen = set()  # Track PKs we've already added
        found_count = 0
        missing = []
        duplicate_pk_count = 0
        
        for inv_num in invoice_numbers:
            if inv_num not in invoice_to_latest_pk:
                missing.append(inv_num)
                append_job_log(job_id, "warning", f"Invoice not found: {inv_num}")
                continue
            
            latest_record = invoice_to_latest_pk[inv_num]
            pk = latest_record["pk"]
            
            # Check if this PK was already added (deduplication)
            if pk in unique_pks_seen:
                duplicate_pk_count += 1
                append_job_log(
                    job_id, 
                    "info", 
                    f"Skipping duplicate PK: {inv_num} → pk={pk} (already processed)"
                )
                continue
            
            # Extract message_id from EMAIL#xxx format
            message_id = pk.replace("EMAIL#", "") if pk.startswith("EMAIL#") else pk
            
            pk_record = {
                "invoice_number": inv_num,
                "pk": pk,
                "message_id": message_id,
                "created_at_iso": latest_record.get("created_at_iso", ""),
                "sk_variants": latest_record.get("sk_variants", []),
                "status": "pending"
            }
            pk_records.append(pk_record)
            unique_pks_seen.add(pk)
            found_count += 1
            
            append_job_log(
                job_id, 
                "info", 
                f"Found: {inv_num} → pk={pk} (created: {latest_record.get('created_at_iso', 'N/A')[:19]}, {len(latest_record.get('sk_variants', []))} SK variants)"
            )

        summary = {
            "total_invoices_requested": len(invoice_numbers),
            "unique_pks_found": found_count,
            "missing_invoices": len(missing),
            "duplicate_pks_skipped": duplicate_pk_count,
            "scanned": scanned_count,
        }

        # Save pk_records to job
        update_retrigger_job(job_id, {"pk_records": pk_records})
        update_job_step(job_id, "fetch_pks", "completed", summary)
        
        append_job_log(
            job_id, 
            "info", 
            f"Found {found_count} unique PK(s) for {len(invoice_numbers)} invoice(s)"
        )
        
        if duplicate_pk_count > 0:
            append_job_log(
                job_id, 
                "info", 
                f"Skipped {duplicate_pk_count} duplicate PK(s) (same PK used by multiple invoices)"
            )
        
        if missing:
            append_job_log(job_id, "warning", f"Missing invoices: {', '.join(missing[:10])}")

        return summary

    except Exception as exc:
        error_msg = str(exc)
        update_job_step(job_id, "fetch_pks", "failed", {"error": error_msg})
        append_job_log(job_id, "error", f"Step 1 failed: {error_msg}")
        raise


def _step_delete_records_ge_workflow(job_id: str, cfg: dict) -> dict:
    """
    Step 2: Query table by each unique pk to get all SK variants (METADATA + ATTACHMENT#).
    Delete each item with conditional expression.
    Matches delete_dynamodb_records.py logic exactly.
    """
    append_job_log(job_id, "info", "Step 2/3: Deleting records from DynamoDB")
    update_job_step(job_id, "delete_records", "running")

    try:
        session = make_source_aws_session()
        dynamodb = session.resource("dynamodb", region_name=cfg["aws_region"])
        table = dynamodb.Table(cfg["dynamodb_table"])

        job = get_retrigger_job(job_id)
        pk_records = job.get("pk_records", [])
        
        # Get unique PKs
        unique_pks = list({r["pk"] for r in pk_records})
        
        # Get progress (for resume support)
        progress = job.get("progress") or {}
        completed_pks = set(progress.get("completed_pks", []))
        
        deleted_count = 0
        failed_count = 0
        total_pks = len(unique_pks)
        
        append_job_log(job_id, "info", f"Deleting records for {total_pks} unique PK(s)")
        
        for idx, pk in enumerate(unique_pks, 1):
            if pk in completed_pks:
                append_job_log(job_id, "info", f"Skipping already deleted: pk={pk}")
                continue
            
            try:
                # Query all items with this PK
                query_resp = table.query(
                    KeyConditionExpression="pk = :pk",
                    ExpressionAttributeValues={":pk": pk}
                )
                
                items_to_delete = query_resp.get("Items", [])
                append_job_log(job_id, "info", f"[{idx}/{total_pks}] Deleting {len(items_to_delete)} item(s) for pk={pk}")
                
                # Delete each item (METADATA + all ATTACHMENT#)
                for item in items_to_delete:
                    sk = item["sk"]
                    try:
                        table.delete_item(
                            Key={"pk": pk, "sk": sk},
                            ConditionExpression="attribute_exists(pk)"
                        )
                        deleted_count += 1
                        append_job_log(job_id, "info", f"  ✓ Deleted: pk={pk} sk={sk}")
                    except Exception as del_exc:
                        failed_count += 1
                        append_job_log(job_id, "warning", f"  ✗ Failed to delete pk={pk} sk={sk}: {del_exc}")
                
                # Mark this PK as completed
                completed_pks.add(pk)
                
                # Update progress after each PK
                update_retrigger_job(job_id, {
                    "progress": {
                        "completed_pks": list(completed_pks),
                        "completed_message_ids": progress.get("completed_message_ids", [])
                    }
                })
                
                # Update step with current progress
                update_job_step(job_id, "delete_records", "running", {
                    "deleted": deleted_count,
                    "failed": failed_count,
                    "total": total_pks,
                    "completed": len(completed_pks)
                })
                
            except Exception as pk_exc:
                failed_count += 1
                append_job_log(job_id, "error", f"Failed to process pk={pk}: {pk_exc}")

        summary = {"deleted": deleted_count, "failed": failed_count, "total": total_pks}
        update_job_step(job_id, "delete_records", "completed", summary)
        append_job_log(job_id, "info", f"Deleted {deleted_count} record(s), {failed_count} failed")

        return summary

    except Exception as exc:
        error_msg = str(exc)
        update_job_step(job_id, "delete_records", "failed", {"error": error_msg})
        append_job_log(job_id, "error", f"Step 2 failed: {error_msg}")
        raise


def _step_reingest_files_ge_workflow(job_id: str, cfg: dict) -> dict:
    """
    Step 3: Trigger S3 events via CopyObject for each unique message_id.
    Copy object to itself with MetadataDirective=REPLACE.
    Matches s3_reingest.py logic exactly with batching and sleep.
    """
    append_job_log(job_id, "info", "Step 3/3: Triggering S3 re-ingestion events")
    update_job_step(job_id, "reingest_files", "running")

    try:
        session = make_source_aws_session()
        s3 = session.client("s3", region_name=cfg["aws_region"])

        job = get_retrigger_job(job_id)
        pk_records = job.get("pk_records", [])
        
        # Collect unique message_ids
        message_ids = list({r["message_id"] for r in pk_records if r.get("message_id")})
        
        if not message_ids:
            update_job_step(job_id, "reingest_files", "completed", {"triggered": 0})
            append_job_log(job_id, "warning", "No message IDs found to reingest")
            return {"triggered": 0, "failed": 0}

        # Get progress (for resume support)
        progress = job.get("progress") or {}
        completed_ids = set(progress.get("completed_message_ids", []))
        
        bucket = cfg["s3_bucket"]
        batch_size = cfg.get("batch_size", 10)
        batch_sleep = cfg.get("batch_sleep_secs", 45)

        triggered = 0
        failed = 0
        total_ids = len(message_ids)
        
        append_job_log(job_id, "info", f"Re-ingesting {total_ids} file(s) in batches of {batch_size}")

        for i in range(0, total_ids, batch_size):
            batch = message_ids[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (total_ids + batch_size - 1) // batch_size

            append_job_log(job_id, "info", f"Processing batch {batch_num}/{total_batches} ({len(batch)} files)")

            for message_id in batch:
                if message_id in completed_ids:
                    append_job_log(job_id, "info", f"  ↷ Skipping already processed: {message_id}")
                    continue
                
                try:
                    # S3 CopyObject triggers Lambda event notifications
                    s3.copy_object(
                        Bucket=bucket,
                        Key=message_id,
                        CopySource={"Bucket": bucket, "Key": message_id},
                        MetadataDirective="REPLACE",
                    )
                    triggered += 1
                    completed_ids.add(message_id)
                    append_job_log(job_id, "info", f"  ✓ Re-ingested: {message_id}")
                except Exception as copy_exc:
                    failed += 1
                    append_job_log(job_id, "error", f"  ✗ Failed to copy {message_id}: {copy_exc}")
            
            # Save progress after each batch
            update_retrigger_job(job_id, {
                "progress": {
                    "completed_pks": progress.get("completed_pks", []),
                    "completed_message_ids": list(completed_ids)
                }
            })
            
            # Update step with current progress
            update_job_step(job_id, "reingest_files", "running", {
                "triggered": triggered,
                "failed": failed,
                "total": total_ids,
                "batch": f"{batch_num}/{total_batches}"
            })

            # Sleep between batches (except after the last batch)
            if i + batch_size < total_ids:
                append_job_log(job_id, "info", f"Sleeping {batch_sleep}s before next batch...")
                time.sleep(batch_sleep)

        summary = {"triggered": triggered, "failed": failed, "total": total_ids}
        update_job_step(job_id, "reingest_files", "completed", summary)
        append_job_log(job_id, "info", f"Triggered {triggered} S3 event(s), {failed} failed")

        # Update job summary
        job = get_retrigger_job(job_id)
        if job:
            steps = job.get("steps", {})
            job_summary = {
                "fetch_pks": steps.get("fetch_pks", {}).get("found", 0),
                "delete_records": steps.get("delete_records", {}).get("deleted", 0),
                "reingest_files": steps.get("reingest_files", {}).get("triggered", 0),
                "failed": failed,
            }
            update_retrigger_job(job_id, {"summary": job_summary})

        return summary

    except Exception as exc:
        error_msg = str(exc)
        update_job_step(job_id, "reingest_files", "failed", {"error": error_msg})
        append_job_log(job_id, "error", f"Step 3 failed: {error_msg}")
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _from_dynamo(obj):
    """Recursively convert DynamoDB Decimal back to float/int."""
    if isinstance(obj, Decimal):
        f = float(obj)
        return int(f) if f == int(f) else f
    if isinstance(obj, dict):
        return {k: _from_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_dynamo(i) for i in obj]
    return obj
