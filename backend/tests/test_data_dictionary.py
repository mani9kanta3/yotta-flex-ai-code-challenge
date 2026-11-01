"""
The data dictionary has to describe the real data. If a field, a type or an allowed value
changes and the document doesn't, these fail.
"""

import csv
import json
import re

import pytest

from app import config
from generator import export
from scripts import validate

DICTIONARY = config.BACKEND_DIR.parent / "docs" / "DATA_DICTIONARY.md"
ALL_TABLES = list(export.TABLE_COLUMNS) + ["inventory_exception"]
HEADER = "| Field | Type | Required | Allowed values | Units | Unique | Validation rule |"


def read_sections():
    """Each section's name, and the rows of its field table as dicts."""
    sections = {}
    name = None
    for line in DICTIONARY.read_text(encoding="utf-8").splitlines():
        if line.startswith("### "):
            name = line[4:].strip()
        elif line.startswith("## "):
            name = None
        elif name and line.startswith("|") and line != HEADER and not line.startswith("|---"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            # The manifest and summary sections have a shorter table, which isn't a list of fields.
            if len(cells) == 7:
                sections.setdefault(name, []).append({
                    "field": cells[0], "type": cells[1], "required": cells[2], "allowed": cells[3],
                    "units": cells[4], "unique": cells[5], "rule": cells[6]})
    return sections


SECTIONS = read_sections()


def type_of(row):
    data_type, length, precision, scale = row
    if data_type == "character varying":
        return f"VARCHAR({length})"
    if data_type == "numeric":
        return f"NUMERIC({precision}, {scale})"
    if data_type == "timestamp without time zone":
        return "TIMESTAMP"
    return data_type.upper()


def database_columns(cursor, table):
    cursor.execute("""SELECT column_name, data_type, character_maximum_length, numeric_precision, numeric_scale,
                             is_nullable
                      FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s
                      ORDER BY ordinal_position""", (table,))
    return cursor.fetchall()


def test_every_table_is_described():
    assert set(ALL_TABLES) <= set(SECTIONS)


@pytest.mark.parametrize("table", ALL_TABLES)
def test_fields_types_and_required_match_the_database(cursor, table):
    in_database = [(row[0], type_of(row[1:5]), "Yes" if row[5] == "NO" else "No")
                   for row in database_columns(cursor, table)]
    in_document = [(row["field"], row["type"], row["required"]) for row in SECTIONS[table]]
    assert in_document == in_database


@pytest.mark.parametrize("table", ALL_TABLES)
def test_every_field_says_everything_the_brief_asks_for(table):
    for row in SECTIONS[table]:
        assert all(row.values()), row


def test_allowed_values_are_the_same_as_the_validator():
    in_document = {}
    for table in ALL_TABLES:
        for row in SECTIONS[table]:
            if row["allowed"].startswith("One of:"):
                in_document[(table, row["field"])] = sorted(re.findall(r"`([^`]+)`", row["allowed"]))
    in_validator = {(table, column): sorted(allowed) for table, column, allowed in validate.ALLOWED_VALUES}
    assert in_document == in_validator


def test_unique_fields_match_the_database(cursor):
    cursor.execute("""
        SELECT t.relname, a.attname FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
        WHERE c.contype IN ('p', 'u') AND cardinality(c.conkey) = 1 AND t.relnamespace = 'public'::regnamespace""")
    in_database = set(cursor.fetchall())
    in_document = {(table, row["field"]) for table in ALL_TABLES for row in SECTIONS[table] if row["unique"] == "Yes"}
    assert in_document == in_database


def test_every_reason_code_written_is_listed(final_cursor):
    text = DICTIONARY.read_text(encoding="utf-8")
    listed = {}
    for table in ("inventory_movement", "allocation_decision", "order_event", "reservation_event", "shipment_event"):
        line = next(line for line in text.splitlines() if line.startswith(f"| `{table}` |"))
        listed[table] = set(re.findall(r"`([A-Z_]+)`", line))
        final_cursor.execute(f"SELECT DISTINCT reason_code FROM {table}")
        assert {row[0] for row in final_cursor.fetchall()} == listed[table], table


def test_every_document_prefix_written_is_listed(final_cursor):
    text = DICTIONARY.read_text(encoding="utf-8")
    final_cursor.execute("SELECT DISTINCT split_part(source_doc, '-', 1) FROM inventory_movement")
    for (prefix,) in final_cursor.fetchall():
        assert f"`{prefix}-2026-`" in text


def test_ground_truth_and_checkpoint_columns_are_described(generated_data):
    truth_dir = export.truth_dir_of(generated_data)
    for section, file_name in (("ground_truth.csv", "ground_truth.csv"), ("atp_checkpoints.csv", "atp_checkpoints.csv")):
        with open(truth_dir / file_name, encoding="utf-8", newline="") as file:
            columns = next(csv.reader(file))
        assert [row["field"] for row in SECTIONS[section]] == columns


def test_manifest_fields_are_described(generated_data):
    manifest = json.loads((generated_data / "source_manifest.json").read_text(encoding="utf-8"))
    text = DICTIONARY.read_text(encoding="utf-8")
    section = text.split("### source_manifest.json")[1].split("### ")[0]
    for field in manifest:
        assert f"`{field}`" in section, field


def test_generation_counters_agree_with_the_tables(generated_data, final_cursor):
    """A manifest can only be trusted if what it counts is what the data holds."""
    counters = json.loads((generated_data / "source_manifest.json").read_text(encoding="utf-8"))["generation_counters"]
    questions = {
        "decisions_single": "SELECT COUNT(*) FROM allocation_decision WHERE decision_code = 'SINGLE'",
        "decisions_split": "SELECT COUNT(*) FROM allocation_decision WHERE decision_code = 'SPLIT'",
        "decisions_inbound": "SELECT COUNT(*) FROM allocation_decision WHERE decision_code = 'INBOUND'",
        "decisions_rejected": "SELECT COUNT(*) FROM allocation_decision WHERE decision_code = 'REJECTED'",
        "decisions_reallocated": "SELECT COUNT(*) FROM allocation_decision WHERE decision_code = 'REALLOCATED'",
        "late_reallocated": "SELECT COUNT(*) FROM reservation_event WHERE reason_code = 'INBOUND_LATE_REALLOCATED' AND to_status = 'CANCELLED'",
        "late_repromised": "SELECT COUNT(*) FROM reservation_event WHERE reason_code = 'INBOUND_LATE_REPROMISED'",
        "late_cancelled": "SELECT COUNT(*) FROM reservation_event WHERE reason_code = 'INBOUND_LATE_CANCELLED'",
        "returns_good": "SELECT COUNT(*) FROM inventory_movement WHERE reason_code = 'RETURN_GOOD'",
        "returns_damaged": "SELECT COUNT(*) FROM inventory_movement WHERE reason_code = 'RETURN_DAMAGED'",
    }
    for counter, sql in questions.items():
        final_cursor.execute(sql)
        assert final_cursor.fetchone()[0] == counters[counter], counter


def test_loader_never_opens_anything_but_the_table_files():
    text = (config.BACKEND_DIR / "scripts" / "load_data.py").read_text(encoding="utf-8")
    assert "validation_summary" not in text
    assert 'data_dir / f"{table}.csv"' in text
