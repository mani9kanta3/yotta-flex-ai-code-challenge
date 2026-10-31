-- Foreign keys and indexes, added after the bulk load.
--
-- Adding a foreign key checks every existing row, so a bad reference in the
-- data makes this file fail loudly.

ALTER TABLE sku ADD FOREIGN KEY (product_id) REFERENCES product (product_id);
ALTER TABLE location ADD FOREIGN KEY (parent_location_id) REFERENCES location (location_id);

ALTER TABLE channel_location ADD FOREIGN KEY (channel_code) REFERENCES channel (channel_code);
ALTER TABLE channel_location ADD FOREIGN KEY (location_id) REFERENCES location (location_id);

ALTER TABLE sku_location ADD FOREIGN KEY (sku_id) REFERENCES sku (sku_id);
ALTER TABLE sku_location ADD FOREIGN KEY (location_id) REFERENCES location (location_id);

ALTER TABLE replenishment_rule ADD FOREIGN KEY (sku_id, location_id) REFERENCES sku_location (sku_id, location_id);
ALTER TABLE replenishment_rule ADD FOREIGN KEY (source_location_id) REFERENCES location (location_id);

ALTER TABLE inventory_balance ADD FOREIGN KEY (sku_id, location_id) REFERENCES sku_location (sku_id, location_id);

ALTER TABLE orders ADD FOREIGN KEY (channel_code) REFERENCES channel (channel_code);

ALTER TABLE order_line ADD FOREIGN KEY (order_id) REFERENCES orders (order_id);
ALTER TABLE order_line ADD FOREIGN KEY (sku_id) REFERENCES sku (sku_id);

ALTER TABLE shipment ADD FOREIGN KEY (order_id) REFERENCES orders (order_id);
ALTER TABLE shipment ADD FOREIGN KEY (from_location_id) REFERENCES location (location_id);
ALTER TABLE shipment ADD FOREIGN KEY (to_location_id) REFERENCES location (location_id);
ALTER TABLE shipment ADD FOREIGN KEY (transfer_sku_id) REFERENCES sku (sku_id);

ALTER TABLE reservation ADD FOREIGN KEY (order_line_id) REFERENCES order_line (order_line_id);
ALTER TABLE reservation ADD FOREIGN KEY (sku_id) REFERENCES sku (sku_id);
ALTER TABLE reservation ADD FOREIGN KEY (sku_id, location_id) REFERENCES sku_location (sku_id, location_id);
ALTER TABLE reservation ADD FOREIGN KEY (inbound_shipment_id) REFERENCES shipment (shipment_id);

ALTER TABLE order_allocation ADD FOREIGN KEY (order_line_id) REFERENCES order_line (order_line_id);
ALTER TABLE order_allocation ADD FOREIGN KEY (location_id) REFERENCES location (location_id);
ALTER TABLE order_allocation ADD FOREIGN KEY (reservation_id) REFERENCES reservation (reservation_id);
ALTER TABLE order_allocation ADD FOREIGN KEY (shipment_id) REFERENCES shipment (shipment_id);

ALTER TABLE inventory_movement ADD FOREIGN KEY (sku_id, location_id) REFERENCES sku_location (sku_id, location_id);
ALTER TABLE inventory_movement ADD FOREIGN KEY (allocation_id) REFERENCES order_allocation (allocation_id);
ALTER TABLE inventory_movement ADD FOREIGN KEY (shipment_id) REFERENCES shipment (shipment_id);
ALTER TABLE inventory_movement ADD FOREIGN KEY (related_movement_id) REFERENCES inventory_movement (movement_id);

ALTER TABLE allocation_decision ADD FOREIGN KEY (order_line_id) REFERENCES order_line (order_line_id);
ALTER TABLE allocation_decision ADD FOREIGN KEY (chosen_location_id) REFERENCES location (location_id);

ALTER TABLE order_event ADD FOREIGN KEY (order_id) REFERENCES orders (order_id);
ALTER TABLE order_event ADD FOREIGN KEY (order_line_id) REFERENCES order_line (order_line_id);
ALTER TABLE reservation_event ADD FOREIGN KEY (reservation_id) REFERENCES reservation (reservation_id);
ALTER TABLE shipment_event ADD FOREIGN KEY (shipment_id) REFERENCES shipment (shipment_id);

ALTER TABLE inventory_exception ADD FOREIGN KEY (balance_id) REFERENCES inventory_balance (balance_id);
ALTER TABLE inventory_exception ADD FOREIGN KEY (reservation_id) REFERENCES reservation (reservation_id);
ALTER TABLE inventory_exception ADD FOREIGN KEY (movement_id) REFERENCES inventory_movement (movement_id);
ALTER TABLE inventory_exception ADD FOREIGN KEY (shipment_id) REFERENCES shipment (shipment_id);

-- The as-of queries all ask for one SKU at one location up to a time.
CREATE INDEX movement_sku_location_time ON inventory_movement (sku_id, location_id, occurred_at);
CREATE INDEX reservation_sku_location ON reservation (sku_id, location_id);
CREATE INDEX reservation_event_lookup ON reservation_event (reservation_id, occurred_at);
CREATE INDEX shipment_event_lookup ON shipment_event (shipment_id, occurred_at);
CREATE INDEX order_event_lookup ON order_event (order_id, occurred_at);
CREATE INDEX order_line_order ON order_line (order_id);
CREATE INDEX allocation_line ON order_allocation (order_line_id);
CREATE INDEX movement_allocation ON inventory_movement (allocation_id);
CREATE INDEX shipment_transfer_destination ON shipment (to_location_id, transfer_sku_id);
