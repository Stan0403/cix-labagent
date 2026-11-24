import csv
import io

from .report_rendering import html_report as html_report
from .report_rendering import markdown_report as markdown_report


def measurements_csv(state):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "session_id",
            "mode",
            "measurement_id",
            "value",
            "unit",
            "source",
            "at",
            "saved",
            "superseded",
            "candidate_id",
            "original_value",
            "reference",
        ]
    )
    for m in state.measurements:
        writer.writerow(
            [
                state.id,
                state.mode,
                m.id,
                m.value,
                m.unit,
                m.source,
                m.at,
                m.saved,
                m.superseded,
                m.candidate_id,
                m.original_value,
                m.reference,
            ]
        )
    return "\ufeff" + buffer.getvalue()
