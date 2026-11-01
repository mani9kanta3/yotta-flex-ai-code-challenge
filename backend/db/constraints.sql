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
CREATE INDEX order_event_line_lookup ON order_event (order_line_id, occurred_at);
CREATE INDEX order_line_order ON order_line (order_id);
CREATE INDEX allocation_line ON order_allocation (order_line_id);
CREATE INDEX movement_allocation ON inventory_movement (allocation_id);
CREATE INDEX shipment_transfer_destination ON shipment (to_location_id, transfer_sku_id);
CREATE INDEX shipment_transfer_sku ON shipment (transfer_sku_id);
CREATE INDEX movement_shipment ON inventory_movement (shipment_id);
CREATE INDEX movement_related ON inventory_movement (related_movement_id);
-- Following one order through its chain, for the trace page.
CREATE INDEX decision_line ON allocation_decision (order_line_id);
CREATE INDEX reservation_line ON reservation (order_line_id);
CREATE INDEX reservation_inbound ON reservation (inbound_shipment_id);
CREATE INDEX shipment_order ON shipment (order_id);

-- Live records carry on numbering after the loaded ones. Otherwise the first
-- transfer shipped after a load would collide with a generated ID.
SELECT setval('shipment_number',
              COALESCE((SELECT MAX(RIGHT(shipment_id, 6)::BIGINT) FROM shipment), 0) + 1, FALSE);
SELECT setval('movement_number',
              COALESCE((SELECT MAX(RIGHT(movement_id, 8)::BIGINT) FROM inventory_movement), 0) + 1, FALSE);
SELECT setval('order_number',
              COALESCE((SELECT MAX(RIGHT(order_id, 6)::BIGINT) FROM orders), 0) + 1, FALSE);
SELECT setval('allocation_number',
              COALESCE((SELECT MAX(RIGHT(allocation_id, 7)::BIGINT) FROM order_allocation), 0) + 1, FALSE);
SELECT setval('decision_number',
              COALESCE((SELECT MAX(RIGHT(decision_id, 7)::BIGINT) FROM allocation_decision), 0) + 1, FALSE);
-- One sequence numbers all three event tables, so it has to pass the highest of them.
SELECT setval('event_number',
              GREATEST((SELECT COALESCE(MAX(event_id), 0) FROM shipment_event),
                       (SELECT COALESCE(MAX(event_id), 0) FROM order_event),
                       (SELECT COALESCE(MAX(event_id), 0) FROM reservation_event)) + 1, FALSE);

-- A bulk load leaves the planner with no row statistics until this runs. At full
-- scale it took the ATP query for the most stocked SKU from 881 ms to 295 ms.
ANALYZE;
