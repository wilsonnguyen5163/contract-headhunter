import json
import logging

from jobmatch.logging_setup import JsonFormatter


def test_one_json_line_with_fields() -> None:
    record = logging.LogRecord("jobmatch.test", logging.INFO, __file__, 1, "run_done", None, None)
    record.fields = {"jobs": 3, "source": "adzuna"}
    line = JsonFormatter().format(record)
    assert "\n" not in line
    payload = json.loads(line)
    assert payload["event"] == "run_done"
    assert payload["jobs"] == 3
    assert payload["ts"].endswith("Z")
