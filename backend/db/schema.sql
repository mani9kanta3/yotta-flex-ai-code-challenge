-- Tables, primary keys, allowed values and immutability triggers.
--
-- Foreign keys and indexes are in constraints.sql. They are added after the
-- bulk load, because COPY is much faster without them.
--
-- A CHECK here only rejects rows that could never be valid. Business mistakes,
-- like a transfer received before it shipped, must still load so the detectors
-- can find them. See docs/PLAN.md.

DROP TABLE IF EXISTS inventory_exception CASCADE;
DROP TABLE IF EXISTS shipment_event CASCADE;
DROP TABLE IF EXISTS reservation_event CASCADE;
DROP TABLE IF EXISTS order_event CASCADE;
DROP TABLE IF EXISTS allocation_decision CASCADE;
DROP TABLE IF EXISTS inventory_movement CASCADE;
DROP TABLE IF EXISTS order_allocation CASCADE;
DROP TABLE IF EXISTS reservation CASCADE;
DROP TABLE IF EXISTS shipment CASCADE;
DROP TABLE IF EXISTS order_line CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS inventory_balance CASCADE;
DROP TABLE IF EXISTS replenishment_rule CASCADE;
DROP TABLE IF EXISTS sku_location CASCADE;
DROP TABLE IF EXISTS channel_location CASCADE;
DROP TABLE IF EXISTS channel CASCADE;
DROP TABLE IF EXISTS location CASCADE;
DROP TABLE IF EXISTS sku CASCADE;
DROP TABLE IF EXISTS product CASCADE;

DROP FUNCTION IF EXISTS refuse_change();

DROP SEQUENCE IF EXISTS order_number;
DROP SEQUENCE IF EXISTS allocation_number;
DROP SEQUENCE IF EXISTS decision_number;
DROP SEQUENCE IF EXISTS event_number;

-- Numbers for orders placed while the system runs. Two transactions can never
-- get the same value, and neither has to wait for the other to get one.
CREATE SEQUENCE order_number;
CREATE SEQUENCE allocation_number;
CREATE SEQUENCE decision_number;
CREATE SEQUENCE event_number;


-- ------------------------------------------------------------ master data

CREATE TABLE product (
    product_id      VARCHAR(10) PRIMARY KEY,
    product_name    VARCHAR(120) NOT NULL,
    category        VARCHAR(30) NOT NULL
        CHECK (category IN ('APPAREL', 'FOOTWEAR', 'HOME', 'ELECTRONICS', 'BEAUTY')),
    brand           VARCHAR(40) NOT NULL,
    status          VARCHAR(15) NOT NULL CHECK (status IN ('ACTIVE', 'DISCONTINUED')),
    launch_date     DATE NOT NULL
);

CREATE TABLE sku (
    sku_id          VARCHAR(10) PRIMARY KEY,
    product_id      VARCHAR(10) NOT NULL,
    size            VARCHAR(10) NOT NULL,
    colour          VARCHAR(20) NOT NULL,
    unit_price      NUMERIC(10, 2) NOT NULL CHECK (unit_price > 0),
    status          VARCHAR(15) NOT NULL CHECK (status IN ('ACTIVE', 'DISCONTINUED')),
    effective_from  DATE NOT NULL,
    effective_to    DATE,
    CHECK (effective_to IS NULL OR effective_to > effective_from)
);

CREATE TABLE location (
    location_id         VARCHAR(10) PRIMARY KEY,
    location_name       VARCHAR(80) NOT NULL,
    location_type       VARCHAR(10) NOT NULL CHECK (location_type IN ('NDC', 'WAREHOUSE', 'STORE')),
    region              VARCHAR(10) NOT NULL
        CHECK (region IN ('NORTH', 'SOUTH', 'EAST', 'WEST', 'CENTRAL')),
    parent_location_id  VARCHAR(10),
    ships_online        BOOLEAN NOT NULL,
    opened_on           DATE NOT NULL,
    closed_on           DATE,
    CHECK (closed_on IS NULL OR closed_on > opened_on),
    -- The NDC is the only location with no parent.
    CHECK ((location_type = 'NDC') = (parent_location_id IS NULL))
);

CREATE TABLE channel (
    channel_code            VARCHAR(15) PRIMARY KEY
        CHECK (channel_code IN ('WEB', 'APP', 'MARKETPLACE', 'STORE')),
    is_online               BOOLEAN NOT NULL,
    counts_inbound          BOOLEAN NOT NULL,
    applies_safety_stock    BOOLEAN NOT NULL,
    -- Null means no hold is placed, which is the STORE channel.
    hold_minutes            INTEGER CHECK (hold_minutes > 0)
);

-- Bridge: channel N:M location, with the buffer that channel keeps back there.
CREATE TABLE channel_location (
    channel_location_id VARCHAR(10) PRIMARY KEY,
    channel_code        VARCHAR(15) NOT NULL,
    location_id         VARCHAR(10) NOT NULL,
    buffer_units        INTEGER NOT NULL CHECK (buffer_units >= 0),
    effective_from      DATE NOT NULL,
    effective_to        DATE,
    CHECK (effective_to IS NULL OR effective_to > effective_from)
);

-- Bridge: sku N:M location. Also the row an order locks, so that two orders
-- can't take the same last unit.
CREATE TABLE sku_location (
    sku_id      VARCHAR(10) NOT NULL,
    location_id VARCHAR(10) NOT NULL,
    ranged_from DATE NOT NULL,
    PRIMARY KEY (sku_id, location_id)
);

CREATE TABLE replenishment_rule (
    rule_id             VARCHAR(11) PRIMARY KEY,
    sku_id              VARCHAR(10) NOT NULL,
    location_id         VARCHAR(10) NOT NULL,
    -- Null means bought from a supplier, which is the NDC.
    source_location_id  VARCHAR(10),
    safety_stock        INTEGER NOT NULL CHECK (safety_stock >= 0),
    reorder_point       INTEGER NOT NULL CHECK (reorder_point >= safety_stock),
    reorder_qty         INTEGER NOT NULL CHECK (reorder_qty > 0),
    lead_time_days      INTEGER NOT NULL CHECK (lead_time_days > 0),
    allow_inbound_atp   BOOLEAN NOT NULL,
    effective_from      DATE NOT NULL,
    effective_to        DATE,
    CHECK (effective_to IS NULL OR effective_to > effective_from)
);

-- Snapshots, never updated. CLOSING is derived from OPENING plus the ledger.
CREATE TABLE inventory_balance (
    balance_id      VARCHAR(11) PRIMARY KEY,
    sku_id          VARCHAR(10) NOT NULL,
    location_id     VARCHAR(10) NOT NULL,
    snapshot_type   VARCHAR(10) NOT NULL CHECK (snapshot_type IN ('OPENING', 'CLOSING')),
    as_of_at        TIMESTAMP NOT NULL,
    on_hand         INTEGER NOT NULL CHECK (on_hand >= 0),
    -- Blocked units are part of on_hand, so blocked can't be larger.
    blocked         INTEGER NOT NULL CHECK (blocked >= 0),
    CHECK (blocked <= on_hand),
    UNIQUE (sku_id, location_id, snapshot_type)
);


-- ------------------------------------------------------------ orders

CREATE TABLE orders (
    order_id        VARCHAR(15) PRIMARY KEY,
    channel_code    VARCHAR(15) NOT NULL,
    -- An opaque code. There is no customer table and no personal data.
    customer_ref    VARCHAR(15) NOT NULL,
    ship_to_region  VARCHAR(10) NOT NULL
        CHECK (ship_to_region IN ('NORTH', 'SOUTH', 'EAST', 'WEST', 'CENTRAL')),
    placed_at       TIMESTAMP NOT NULL,
    status          VARCHAR(15) NOT NULL
        CHECK (status IN ('PLACED', 'ALLOCATED', 'CONFIRMED', 'FULFILLED', 'CANCELLED', 'REJECTED')),
    order_total     NUMERIC(12, 2) NOT NULL CHECK (order_total >= 0)
);

CREATE TABLE order_line (
    order_line_id   VARCHAR(20) PRIMARY KEY,
    order_id        VARCHAR(15) NOT NULL,
    line_no         INTEGER NOT NULL CHECK (line_no > 0),
    sku_id          VARCHAR(10) NOT NULL,
    qty             INTEGER NOT NULL CHECK (qty > 0),
    unit_price      NUMERIC(10, 2) NOT NULL CHECK (unit_price > 0),
    line_amount     NUMERIC(12, 2) NOT NULL,
    status          VARCHAR(15) NOT NULL
        CHECK (status IN ('PENDING', 'ALLOCATED', 'SHIPPED', 'CANCELLED', 'REJECTED')),
    CHECK (line_amount = qty * unit_price),
    UNIQUE (order_id, line_no)
);

CREATE TABLE shipment (
    shipment_id         VARCHAR(15) PRIMARY KEY,
    shipment_type       VARCHAR(10) NOT NULL CHECK (shipment_type IN ('CUSTOMER', 'TRANSFER')),
    status              VARCHAR(15) NOT NULL
        CHECK (status IN ('CREATED', 'IN_TRANSIT', 'DELIVERED', 'RECEIVED', 'CANCELLED')),
    order_id            VARCHAR(15),
    from_location_id    VARCHAR(10) NOT NULL,
    to_location_id      VARCHAR(10),
    created_at          TIMESTAMP NOT NULL,
    -- The first ETA. A later ETA is kept in shipment_event.
    original_eta_at     TIMESTAMP,
    transfer_sku_id     VARCHAR(10),
    transfer_qty        INTEGER CHECK (transfer_qty > 0),
    -- A customer shipment belongs to an order, and a transfer has one SKU.
    CHECK (
        (shipment_type = 'CUSTOMER' AND order_id IS NOT NULL AND to_location_id IS NULL
            AND transfer_sku_id IS NULL AND transfer_qty IS NULL)
        OR
        (shipment_type = 'TRANSFER' AND order_id IS NULL AND to_location_id IS NOT NULL
            AND transfer_sku_id IS NOT NULL AND transfer_qty IS NOT NULL)
    )
);

-- This row only holds facts that never change, plus status as a copy of the
-- latest event. State at a past time always comes from reservation_event.
CREATE TABLE reservation (
    reservation_id      VARCHAR(25) PRIMARY KEY,
    order_line_id       VARCHAR(20) NOT NULL,
    sku_id              VARCHAR(10) NOT NULL,
    -- Null only for a REJECTED request that had no candidate location.
    location_id         VARCHAR(10),
    qty                 INTEGER NOT NULL CHECK (qty > 0),
    supply_type         VARCHAR(10) NOT NULL CHECK (supply_type IN ('ON_HAND', 'INBOUND')),
    inbound_shipment_id VARCHAR(15),
    created_at          TIMESTAMP NOT NULL,
    expires_at          TIMESTAMP NOT NULL,
    -- The first promise. A later promise is kept in reservation_event.
    promised_ship_by    TIMESTAMP,
    status              VARCHAR(15) NOT NULL
        CHECK (status IN ('ACTIVE', 'CONFIRMED', 'CONSUMED', 'EXPIRED', 'CANCELLED', 'REJECTED')),
    CHECK ((supply_type = 'INBOUND') = (inbound_shipment_id IS NOT NULL)),
    CHECK (location_id IS NOT NULL OR status = 'REJECTED')
);

-- Bridge: order_line N:M location.
CREATE TABLE order_allocation (
    allocation_id   VARCHAR(16) PRIMARY KEY,
    order_line_id   VARCHAR(20) NOT NULL,
    location_id     VARCHAR(10) NOT NULL,
    qty             INTEGER NOT NULL CHECK (qty > 0),
    -- Null for a STORE order, which places no hold.
    reservation_id  VARCHAR(25),
    -- Null until shipped, and always null for a STORE handover.
    shipment_id     VARCHAR(15),
    allocated_at    TIMESTAMP NOT NULL,
    UNIQUE (reservation_id)
);


-- ------------------------------------------------------------ the ledger

CREATE TABLE inventory_movement (
    movement_id         VARCHAR(17) PRIMARY KEY,
    sku_id              VARCHAR(10) NOT NULL,
    location_id         VARCHAR(10) NOT NULL,
    movement_type       VARCHAR(15) NOT NULL,
    stock_bucket        VARCHAR(10) NOT NULL CHECK (stock_bucket IN ('SELLABLE', 'BLOCKED')),
    -- Signed. The change to this one bucket.
    quantity            INTEGER NOT NULL CHECK (quantity <> 0),
    occurred_at         TIMESTAMP NOT NULL,
    reason_code         VARCHAR(30) NOT NULL,
    source_doc          VARCHAR(25) NOT NULL,
    -- Ties the two rows of a BLOCK or UNBLOCK together.
    movement_group_id   VARCHAR(15),
    allocation_id       VARCHAR(16),
    shipment_id         VARCHAR(15),
    -- A return points at the sale it came from.
    related_movement_id VARCHAR(17),
    -- SALE allows BLOCKED so the labelled anomaly can load. Everything else
    -- here is a physical fact about which way stock moves.
    CHECK (
        (movement_type = 'RECEIPT'      AND stock_bucket = 'SELLABLE' AND quantity > 0)
        OR (movement_type = 'SALE'      AND quantity < 0)
        OR (movement_type = 'RETURN'    AND quantity > 0)
        OR (movement_type = 'TRANSFER_OUT' AND stock_bucket = 'SELLABLE' AND quantity < 0)
        OR (movement_type = 'TRANSFER_IN'  AND stock_bucket = 'SELLABLE' AND quantity > 0)
        OR (movement_type = 'ADJUSTMENT'   AND stock_bucket = 'SELLABLE')
        OR (movement_type = 'BLOCK'     AND ((stock_bucket = 'SELLABLE' AND quantity < 0)
                                          OR (stock_bucket = 'BLOCKED'  AND quantity > 0)))
        OR (movement_type = 'UNBLOCK'   AND ((stock_bucket = 'BLOCKED'  AND quantity < 0)
                                          OR (stock_bucket = 'SELLABLE' AND quantity > 0)))
        OR (movement_type = 'WRITE_OFF' AND stock_bucket = 'BLOCKED'  AND quantity < 0)
    ),
    CHECK ((movement_type IN ('BLOCK', 'UNBLOCK')) = (movement_group_id IS NOT NULL))
);


-- ------------------------------------------------------------ decisions and history

CREATE TABLE allocation_decision (
    decision_id         VARCHAR(16) PRIMARY KEY,
    order_line_id       VARCHAR(20) NOT NULL,
    decided_at          TIMESTAMP NOT NULL,
    requested_qty       INTEGER NOT NULL CHECK (requested_qty > 0),
    decision_code       VARCHAR(20) NOT NULL
        CHECK (decision_code IN ('SINGLE', 'SPLIT', 'INBOUND', 'REJECTED', 'REALLOCATED')),
    reason_code         VARCHAR(40) NOT NULL,
    chosen_location_id  VARCHAR(10),
    atp_seen            INTEGER,
    promisable_seen     INTEGER,
    -- The top candidates and their numbers, as evidence for the decision.
    candidates          JSONB
);

CREATE TABLE order_event (
    event_id        BIGINT PRIMARY KEY,
    order_id        VARCHAR(15) NOT NULL,
    -- Null when the event is about the whole order.
    order_line_id   VARCHAR(20),
    from_status     VARCHAR(15),
    to_status       VARCHAR(15) NOT NULL,
    occurred_at     TIMESTAMP NOT NULL,
    reason_code     VARCHAR(40) NOT NULL
);

CREATE TABLE reservation_event (
    event_id            BIGINT PRIMARY KEY,
    reservation_id      VARCHAR(25) NOT NULL,
    from_status         VARCHAR(15),
    to_status           VARCHAR(15) NOT NULL
        CHECK (to_status IN ('ACTIVE', 'CONFIRMED', 'CONSUMED', 'EXPIRED', 'CANCELLED', 'REJECTED')),
    occurred_at         TIMESTAMP NOT NULL,
    reason_code         VARCHAR(40) NOT NULL,
    -- Set when this event changes the promised date.
    promised_ship_by    TIMESTAMP
);

CREATE TABLE shipment_event (
    event_id        BIGINT PRIMARY KEY,
    shipment_id     VARCHAR(15) NOT NULL,
    from_status     VARCHAR(15),
    to_status       VARCHAR(15) NOT NULL
        CHECK (to_status IN ('CREATED', 'IN_TRANSIT', 'DELIVERED', 'RECEIVED', 'CANCELLED')),
    occurred_at     TIMESTAMP NOT NULL,
    reason_code     VARCHAR(40) NOT NULL,
    -- Set when this event changes the ETA.
    eta_at          TIMESTAMP
);

CREATE TABLE inventory_exception (
    exception_id        VARCHAR(10) PRIMARY KEY,
    issue_type          VARCHAR(40) NOT NULL,
    balance_id          VARCHAR(11),
    reservation_id      VARCHAR(25),
    movement_id         VARCHAR(17),
    shipment_id         VARCHAR(15),
    expected_value      INTEGER,
    actual_value        INTEGER,
    recommended_action  VARCHAR(60) NOT NULL,
    status              VARCHAR(10) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'RESOLVED')),
    detected_at         TIMESTAMP NOT NULL,
    -- Each exception points at exactly one record.
    CHECK (num_nonnulls(balance_id, reservation_id, movement_id, shipment_id) = 1)
);


-- ------------------------------------------------------------ immutability

-- History can only be added to. The database itself refuses a change, so no
-- bug in Python can rewrite it.
CREATE FUNCTION refuse_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only, % refused', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER inventory_movement_append_only BEFORE UPDATE OR DELETE ON inventory_movement
    FOR EACH ROW EXECUTE FUNCTION refuse_change();
CREATE TRIGGER inventory_balance_append_only BEFORE UPDATE OR DELETE ON inventory_balance
    FOR EACH ROW EXECUTE FUNCTION refuse_change();
CREATE TRIGGER allocation_decision_append_only BEFORE UPDATE OR DELETE ON allocation_decision
    FOR EACH ROW EXECUTE FUNCTION refuse_change();
CREATE TRIGGER order_event_append_only BEFORE UPDATE OR DELETE ON order_event
    FOR EACH ROW EXECUTE FUNCTION refuse_change();
CREATE TRIGGER reservation_event_append_only BEFORE UPDATE OR DELETE ON reservation_event
    FOR EACH ROW EXECUTE FUNCTION refuse_change();
CREATE TRIGGER shipment_event_append_only BEFORE UPDATE OR DELETE ON shipment_event
    FOR EACH ROW EXECUTE FUNCTION refuse_change();
