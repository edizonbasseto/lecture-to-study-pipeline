"""Operational support shared by the pipeline stages: the ingestion queue
writer, process lifecycle and environment control, model attestation, and
the LLM instruction and response contracts."""

from lecture_study.ops.catalog_bridge import (
    QUEUE_PATH,
    CatalogBridgeError,
    upsert_lecture_in_queue,
)

__all__ = ["QUEUE_PATH", "CatalogBridgeError", "upsert_lecture_in_queue"]
