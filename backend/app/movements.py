"""
Writes one row to the ledger.

The table is append-only, so a wrong row can't be fixed later. Every live change to
stock comes through this one function, each from a caller that has already checked it.
"""

from .orders import next_id


def add_movement(cursor, sku_id, location_id, movement_type, quantity, occurred_at, reason, source_doc,
                 bucket="SELLABLE", allocation_id=None, shipment_id=None, related_movement_id=None):
    movement_id = f"MOV-2026-{next_id(cursor, 'movement_number'):08d}"
    cursor.execute(
        """INSERT INTO inventory_movement
           (movement_id, sku_id, location_id, movement_type, stock_bucket, quantity, occurred_at,
            reason_code, source_doc, allocation_id, shipment_id, related_movement_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (movement_id, sku_id, location_id, movement_type, bucket, quantity, occurred_at, reason,
         source_doc, allocation_id, shipment_id, related_movement_id),
    )
    return movement_id
