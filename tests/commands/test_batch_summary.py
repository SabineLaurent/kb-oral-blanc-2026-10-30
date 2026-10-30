"""The summaries of the batch commands: the table, its rule, the duration of the run."""

import pytest

from k_b.commands.batch_summary import describe_duration, render_rule, render_table


def test_a_table_pads_each_column_to_its_widest_cell():
    lines = render_table(
        [
            ("source", "raw files", "note"),
            ("tldr_ai", "6", ""),
            ("cisa_kev", "12", "x"),
        ]
    )

    assert lines == [
        "source    raw files  note",
        "tldr_ai   6",
        "cisa_kev  12         x",
    ]
    assert render_rule(lines) == "-" * len("source    raw files  note")


@pytest.mark.parametrize(
    ("seconds", "duration"),
    [(0.4, "0 s"), (57, "57 s"), (60, "1 min 0 s"), (246.2, "4 min 6 s")],
)
def test_the_duration_of_a_run_is_in_seconds_then_minutes(seconds, duration):
    assert describe_duration(seconds) == duration
