"""
target-booksy
=============
Reads JournalEntries.csv, transforms each row into a Business Central
general journal line payload, and POSTs them to the BC Chargebee API.

Two-step flow (mirrors target-intacct):
  1. Authenticate → Microsoft OAuth2 client_credentials → Bearer token
  2. POST each line → bc_gj_lines → creates GJ entry in Business Central

One API call is made per CSV row (each row = one bc_gj_line).
Rows sharing the same Posting Id share the same ext_document_no in BC,
grouping them as one logical journal entry.

Execution flow
--------------
1. Parse --config config.json        (singer.utils.parse_args)
2. BusinessCentralClient.__init__()  → _authenticate() → Bearer token
3. load_csv()                        → validate columns
4. transform_to_bc_lines()           → list of bc_gj_lines payloads
5. For each line: bc_client.post_entry()
6. Log summary: N succeeded, M failed
"""

from __future__ import annotations

import json
import logging
import math
import sys
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import singer

from .client import get_client
from .const import (
    BC_ACCOUNT_NO,
    BC_AMOUNT,
    BC_BAL_ACCOUNT_NO,
    BC_CURRENCY_CODE,
    BC_DIM_COST_CENTER,
    BC_DIM_COUNTRY,
    BC_DIM_PROJECT,
    BC_DIM_TAX,
    BC_DESCRIPTION,
    BC_DOCUMENT_DATE,
    BC_EXT_DOCUMENT_NO,
    BC_POSTING_DATE,
    BATCH_SIZE,
    COL_ACCOUNT_NAME,
    COL_ACCOUNT_NUMBER,
    COL_AMOUNT,
    COL_CURRENCY_CODE,
    COL_CUSTOMER_ID,
    COL_DIM_COST_CENTER,
    COL_DIM_COUNTRY,
    COL_DIM_PROJECT,
    COL_DIM_TAX,
    COL_LAST_MODIFIED,
    COL_POSTING_GROUP_ID,
    COL_POSTING_ID,
    COL_PRODUCT_ID,
    COL_STATE,
    COL_TRANSACTION_DATE,
    COL_TYPE,
    DEFAULT_BAL_ACCOUNT_NO,
    DEFAULT_DESCRIPTION,
    INPUT_FILENAME,
    POSTING_TYPE_CREDIT,
    POSTING_TYPE_DEBIT,
    REQUIRED_CONFIG_KEYS,
    REQUIRED_CSV_COLS,
)
from .exceptions import BooksynTargetError, TransformError

logger = singer.get_logger()


# ── Safe value helpers ──────────────────────────────────────────────────────────

def _safe_str(val) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    s = str(val).strip()
    return s if s else None


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


# ── Transformation ──────────────────────────────────────────────────────────────

def transform_to_bc_lines(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Transform each CSV row into a Business Central bc_gj_lines payload.

    One output dict = one API call = one GJ line in Business Central.
    Rows sharing the same Posting Id will share the same ext_document_no,
    grouping them as one logical journal entry in BC.

    Fixed values (hardcoded):
      bal_account_no → DEFAULT_BAL_ACCOUNT_NO
      description    → DEFAULT_DESCRIPTION

    Dim columns (read from CSV if header present, else ""):
      dim_cost_center, dim_country, dim_project, dim_tax

    Amount sign convention (Business Central):
      Debit  → positive amount
      Credit → negative amount

    Returns
    -------
    list of dict
        One dict per CSV row, ready to POST to bc_gj_lines.

    Raises
    ------
    TransformError
        If any row has an invalid amount, missing account, or bad type.
        All errors collected before raising.
    """
    lines: List[Dict] = []
    errors: List[str] = []

    cols = set(df.columns)

    for idx, row in df.iterrows():
        posting_id   = _safe_str(row.get(COL_POSTING_ID))
        txn_date     = _safe_str(row.get(COL_TRANSACTION_DATE))
        account_no   = _safe_str(row.get(COL_ACCOUNT_NUMBER))
        posting_type = _safe_str(row.get(COL_TYPE, "")).upper()
        currency     = _safe_str(row.get(COL_CURRENCY_CODE))
        amount_raw   = _safe_float(row.get(COL_AMOUNT))

        # ── Validation ──────────────────────────────────────────────────────
        if not account_no:
            errors.append(f"Row {idx}: '{COL_ACCOUNT_NUMBER}' is empty.")

        if amount_raw is None:
            errors.append(f"Row {idx}: '{COL_AMOUNT}' is missing or non-numeric.")

        if posting_type not in (POSTING_TYPE_DEBIT, POSTING_TYPE_CREDIT):
            errors.append(
                f"Row {idx}: '{COL_TYPE}' must be Debit or Credit, got '{posting_type}'."
            )

        # ── Amount sign ─────────────────────────────────────────────────────
        # BC convention: Debit = positive, Credit = negative
        if amount_raw is not None and posting_type == POSTING_TYPE_CREDIT:
            signed_amount = -round(amount_raw, 6)
        elif amount_raw is not None:
            signed_amount = round(amount_raw, 6)
        else:
            signed_amount = None

        # ── Dim columns — read from CSV if header present, else "" ───────────
        dim_cost_center = (_safe_str(row[COL_DIM_COST_CENTER]) or "") if COL_DIM_COST_CENTER in cols else ""
        dim_country     = (_safe_str(row[COL_DIM_COUNTRY])     or "") if COL_DIM_COUNTRY     in cols else ""
        dim_project     = (_safe_str(row[COL_DIM_PROJECT])     or "") if COL_DIM_PROJECT     in cols else ""
        dim_tax         = (_safe_str(row[COL_DIM_TAX])         or "") if COL_DIM_TAX         in cols else ""

        # ── Build BC payload ─────────────────────────────────────────────────
        # BC limit: ext_document_no max 35 chars
        ext_doc_no = (posting_id or "")[:35]

        lines.append({
            BC_POSTING_DATE:    txn_date,                # ← CSV: Transaction Date
            BC_DOCUMENT_DATE:   txn_date,                # ← CSV: Transaction Date
            BC_EXT_DOCUMENT_NO: ext_doc_no,              # ← CSV: Posting Id (max 35)
            BC_ACCOUNT_NO:      account_no,              # ← CSV: Account Number
            BC_BAL_ACCOUNT_NO:  DEFAULT_BAL_ACCOUNT_NO,  # ← hardcoded
            BC_DESCRIPTION:     DEFAULT_DESCRIPTION,     # ← hardcoded
            BC_AMOUNT:          signed_amount,           # ← CSV: Amount (signed by Type)
            BC_CURRENCY_CODE:   currency,                # ← CSV: Currency Code
            BC_DIM_COST_CENTER: dim_cost_center,         # ← CSV col or ""
            BC_DIM_COUNTRY:     dim_country,             # ← CSV col or ""
            BC_DIM_PROJECT:     dim_project,             # ← CSV col or ""
            BC_DIM_TAX:         dim_tax,                 # ← CSV col or ""
        })

    if errors:
        for e in errors:
            logger.error(e)
        raise TransformError(
            f"Transformation failed with {len(errors)} error(s). See logs above."
        )

    logger.info(
        "Transformed %d CSV rows → %d bc_gj_lines payloads.",
        len(df), len(lines),
    )
    return lines


# ── CSV loading ─────────────────────────────────────────────────────────────────

def load_csv(input_path: str) -> pd.DataFrame:
    """Read and validate JournalEntries.csv. Exits cleanly on errors."""
    filepath = f"{input_path.rstrip('/')}/{INPUT_FILENAME}"
    logger.info("Reading input file: %s", filepath)

    try:
        df = pd.read_csv(filepath)
    except FileNotFoundError:
        logger.error("Input file not found: %s", filepath)
        sys.exit(1)

    missing = [c for c in REQUIRED_CSV_COLS if c not in df.columns]
    if missing:
        logger.error(
            "CSV is missing required columns: %s  Found: %s",
            json.dumps(missing),
            json.dumps(list(df.columns)),
        )
        sys.exit(1)

    logger.info(
        "Loaded %d rows, %d unique Posting Ids.",
        len(df), df[COL_POSTING_ID].nunique(),
    )
    return df


# ── Orchestration ───────────────────────────────────────────────────────────────

def upload(config: Dict, bc_client) -> None:
    """
    Main pipeline:
    1. Load + validate CSV
    2. Transform rows → bc_gj_lines payloads
    3. Split into chunks of BATCH_SIZE (100) and POST each chunk to $batch
    4. Log per-batch and overall summary
    """
    # 1. Load
    df = load_csv(config["input_path"])

    # 2. Transform
    logger.info("Transforming CSV rows to BC journal line payloads...")
    lines = transform_to_bc_lines(df)

    total      = len(lines)
    chunks     = [lines[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
    num_chunks = len(chunks)

    logger.info(
        "Posting %d lines in %d batch(es) of up to %d records each...",
        total, num_chunks, BATCH_SIZE,
    )

    # 3. POST in chunks
    all_succeeded = 0
    all_failed: List[Dict] = []

    for batch_num, chunk in enumerate(chunks, start=1):
        logger.info(
            "--- Batch %d/%d: sending %d records ---", batch_num, num_chunks, len(chunk)
        )
        try:
            succeeded, failed = bc_client.post_batch(chunk)
            all_succeeded += len(succeeded)
            all_failed.extend(failed)
            logger.info(
                "Batch %d/%d complete: %d succeeded, %d failed.",
                batch_num, num_chunks, len(succeeded), len(failed),
            )
        except BooksynTargetError as exc:
            # Entire batch HTTP-level failure — mark all records in chunk as failed
            logger.error(
                "Batch %d/%d HTTP-level failure: %s", batch_num, num_chunks, exc.message
            )
            all_failed.extend([
                {"id": str(i + 1), "status": "http_error", "error": exc.message}
                for i in range(len(chunk))
            ])

    # 4. Summary
    logger.info(
        "Done. %d/%d lines posted successfully to Business Central.",
        all_succeeded, total,
    )
    if all_failed:
        logger.error(
            "%d lines FAILED across all batches. Failed ids: %s",
            len(all_failed),
            [f["id"] for f in all_failed],
        )
        sys.exit(1)


# ── Entry point ─────────────────────────────────────────────────────────────────

@singer.utils.handle_top_exception(logger)
def main() -> None:
    args = singer.utils.parse_args(REQUIRED_CONFIG_KEYS)
    config = args.config

    logger.info("Starting target-booksy.")

    bc_client = get_client(
        tenant_domain = config["tenant_domain"],
        client_id     = config["client_id"],
        client_secret = config["client_secret"],
        environment   = config["environment"],
        company_id    = config["company_id"],
        timeout       = config.get("timeout_seconds", 30),
    )

    upload(config, bc_client)

    logger.info("target-booksy finished.")


if __name__ == "__main__":
    main()
