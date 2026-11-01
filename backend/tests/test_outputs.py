"""
The commands that produce the deliverables: the one-command run, the measured report and the
SQL and JSON seed files.
"""

import json
import subprocess

import pytest

from app import db
from eval import report
from generator.export import TABLE_COLUMNS
from scripts import export_seed, run_all, setup_database
from tests.conftest import require_postgres

SEED_DB = "inventory_test_seed"


# ------------------------------------------------------------ run_all

def test_run_all_expects_the_final_data_to_fail_validation():
    """Exit 0 there would mean the validator missed the labelled problems."""
    steps = {name: code for name, _command, code in run_all.steps_for("starter", 1, False)}
    assert steps["validate final data"] == 1
    assert steps["validate clean baseline"] == 0
    assert all(code == 0 for name, code in steps.items() if name != "validate final data")


def test_run_all_proves_the_clean_baseline_before_the_final_data():
    names = [name for name, _command, _code in run_all.steps_for("starter", 1, False)]
    assert names.index("validate clean baseline") < names.index("load final data")
    assert names[-1] == "same seed twice"


def test_run_all_can_skip_the_slow_comparison():
    names = [name for name, _command, _code in run_all.steps_for("starter", 1, True)]
    assert "same seed twice" not in names


@pytest.mark.parametrize("actual, expected", [(1, 0), (0, 1)])
def test_run_all_stops_when_a_step_gives_the_wrong_exit_code(monkeypatch, actual, expected):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, actual))
    with pytest.raises(SystemExit) as stopped:
        run_all.run_step("a step", ["eval.score"], expected)
    assert stopped.value.code == 1


def test_run_all_carries_on_when_a_step_gives_the_right_exit_code(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 1))
    assert run_all.run_step("a step", ["scripts.validate"], 1) >= 0


# ------------------------------------------------------------ the report

@pytest.fixture
def summary(generated_data, final_cursor):
    return report.build_summary(final_cursor, generated_data)


def test_report_holds_no_record_ids(summary):
    """The API serves this file, and the API must never be able to see the answers."""
    text = json.dumps(summary, default=str)
    for prefix in ("RES-ORD", "MOV-2026", "BAL-0", "SHP-2026", "ALC-2026", "ORD-2026"):
        assert prefix not in text


def test_report_measures_traceability(summary):
    assert summary["traceability"]["allocation_share"] == 1.0
    assert summary["traceability"]["shipment_share"] == 1.0


def test_report_counts_every_oversell_attempt_as_blocked(summary):
    assert summary["oversell"]["attempts"] > 0
    assert summary["oversell"]["blocked_rate"] == 1.0


def test_report_shares_add_up(summary):
    assert sum(row["decisions"] for row in summary["decisions"]["by_code"]) == summary["decisions"]["total"]
    assert sum(row["movements"] for row in summary["mix"]["movements"]) == summary["dataset"]["files"]["inventory_movement.csv"]["rows"]
    assert sum(row["orders"] for row in summary["mix"]["channels"]) == summary["dataset"]["files"]["orders.csv"]["rows"]


def test_report_says_nothing_unrelated_changed(summary):
    assert summary["unrelated_problems"] == []


def test_report_covers_the_months_of_the_data(summary):
    assert [row["month"] for row in summary["mix"]["months"]][0] == "2026-01"
    assert [row["month"] for row in summary["mix"]["months"]][-1] == "2026-09"


def test_share_of_nothing_is_not_a_number():
    assert report.share(1, 0) is None


# ------------------------------------------------------------ SQL and JSON seed files

def test_seed_sql_quotes_text_and_writes_null():
    assert export_seed.sql_value("O'Brien") == "'O''Brien'"
    assert export_seed.sql_value(None) == "NULL"


def test_seed_json_has_every_table_and_every_row(generated_data, tmp_path):
    export_seed.export_seed(generated_data, tmp_path)
    tables = json.loads((tmp_path / "seed.json").read_text(encoding="utf-8"))
    manifest = json.loads((generated_data / "source_manifest.json").read_text(encoding="utf-8"))
    assert set(tables) == set(TABLE_COLUMNS)
    for table, rows in tables.items():
        assert len(rows) == manifest["files"][f"{table}.csv"]["rows"]


def test_seed_sql_loads_and_gives_the_same_row_counts(generated_data, tmp_path):
    require_postgres()
    export_seed.export_seed(generated_data, tmp_path)
    setup_database.ensure_database(SEED_DB)
    setup_database.run_sql_file("schema.sql", SEED_DB)

    connection = db.get_connection(SEED_DB)
    with connection, connection.cursor() as cursor:
        cursor.execute((tmp_path / "seed.sql").read_text(encoding="utf-8"))
    connection.close()
    # Foreign keys go on last, so this also shows every reference in the file is good.
    setup_database.run_sql_file("constraints.sql", SEED_DB)

    manifest = json.loads((generated_data / "source_manifest.json").read_text(encoding="utf-8"))
    connection = db.get_connection(SEED_DB)
    with connection.cursor() as cursor:
        for table in TABLE_COLUMNS:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            assert cursor.fetchone()[0] == manifest["files"][f"{table}.csv"]["rows"], table
    connection.close()
