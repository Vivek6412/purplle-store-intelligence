import csv
import json
import logging
from pathlib import Path
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import AsyncSessionLocal
from app.models import PosTransaction, Store

logger = logging.getLogger("store_intelligence")
settings = get_settings()

async def load_store_layout() -> None:
    layout_path = Path(settings.store_layout_path)
    if not layout_path.exists():
        logger.warning(f"store_layout path not found at {layout_path} — skipping")
        return

    stores = []
    if layout_path.is_dir():
        for json_file in layout_path.glob("*.json"):
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    stores.extend(data)
                else:
                    stores.append(data)
    else:
        with open(layout_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                stores.extend(data)
            else:
                stores.append(data)
    inserted = 0
    skipped = 0

    async with AsyncSessionLocal() as db:
        for entry in stores:
            store_id = entry.get("store_id") or entry.get("id")
            if not store_id:
                logger.warning("store_layout.json entry missing store_id — skipping")
                skipped += 1
                continue

            existing = await db.get(Store, store_id)
            if existing:
                skipped += 1
                continue

            store = Store(
                store_id=store_id,
                layout_json=entry,
                open_time=_parse_time(entry.get("open_time", "08:00")),
                close_time=_parse_time(entry.get("close_time", "22:00")),
                timezone=entry.get("timezone", "Asia/Kolkata"),
            )
            db.add(store)
            inserted += 1

        await db.commit()
    logger.info(f"load_store_layout complete: inserted={inserted} skipped={skipped}")

async def load_pos_transactions(db: AsyncSession, csv_path: str) -> None:
    csv_file = Path(csv_path)
    if not csv_file.exists():
        logger.warning(f"pos_transactions.csv not found at {csv_file} — skipping")
        return

    inserted = 0
    skipped = 0
    errors = 0

    with open(csv_file, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                # Robustly map headers based on Purplle Store Resources
                store_id = row.get("store_id") or row.get("Store ID")
                
                # Transaction ID
                txn_id = row.get("transaction_id") or row.get("order_id") or row.get("invoice_number")
                
                # Basket Value
                val_raw = row.get("basket_value_inr") or row.get("total_amount") or row.get("GMV") or 0
                basket_val = float(val_raw)
                
                # Timestamp parsing
                ts_raw = row.get("timestamp")
                if ts_raw:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                else:
                    # Combine order_date and order_time from Brigade samples
                    od = row.get("order_date")
                    ot = row.get("order_time")
                    if od and ot:
                        # Format like 10-04-2026 12:15:05
                        dt_str = f"{od} {ot}"
                        try:
                            ts = datetime.strptime(dt_str, "%d-%m-%Y %H:%M:%S")
                        except ValueError:
                            # Fallback if different format
                            ts = datetime.fromisoformat(dt_str)
                    else:
                        ts = datetime.utcnow()
                
                if not store_id or not txn_id:
                    continue

                stmt = pg_insert(PosTransaction).values(
                    store_id=store_id,
                    transaction_id=str(txn_id),
                    ts=ts,
                    basket_value=basket_val,
                    matched_visitor=None,
                )
                stmt = stmt.on_conflict_do_nothing(index_elements=["transaction_id"])
                result = await db.execute(stmt)

                if result.rowcount == 0:
                    skipped += 1
                else:
                    inserted += 1

            except Exception as exc:
                logger.warning(f"Error inserting POS row {row}: {exc}")
                errors += 1

    await db.commit()
    logger.info(f"load_pos_transactions complete: inserted={inserted} skipped={skipped} errors={errors}")

def _parse_time(time_str: str):
    from datetime import time
    parts = time_str.split(":")
    return time(int(parts[0]), int(parts[1]))
