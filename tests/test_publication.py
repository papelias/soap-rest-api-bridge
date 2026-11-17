"""Offline reproducibility checks for the artifacts shipped with the project."""

import hashlib
import json
from io import BytesIO
from pathlib import Path

import pytest
from zeep.transports import Transport
from zeep.wsdl import Document

from soap_rest_ai.evaluation.runner import compare_runs, render_markdown, summarize
from soap_rest_ai.soap.catalog import extract_catalog, serialize_catalog

ROOT = Path(__file__).parents[1]


def test_stored_catalog_regenerates_from_public_wsdl_snapshot() -> None:
    expected_bytes = (ROOT / "data/countryinfo_catalog.json").read_bytes()
    expected = json.loads(expected_bytes)
    snapshot = (ROOT / "tests/fixtures/countryinfo.wsdl").read_bytes()
    transport = Transport()
    try:
        # The base URI preserves public source provenance without fetching it.
        wsdl = Document(BytesIO(snapshot), transport=transport, base=expected["wsdl_url"])
        assert serialize_catalog(extract_catalog(wsdl)).encode("utf-8") == expected_bytes
    finally:
        transport.session.close()


@pytest.mark.parametrize("label", ["baseline", "improved-01"])
def test_saved_eval_artifacts_and_reports_are_reproducible(label: str) -> None:
    runs = ROOT / "data/evals/runs"
    folder = runs / label
    result = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert result["manifest"] == manifest
    for name, key in [
        ("cases.json", "dataset_sha256"),
        ("catalog.json", "catalog_sha256"),
        ("source_snapshot.json", "source_sha256"),
    ]:
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == manifest[key]
    assert (folder / "cases.json").read_bytes() == (ROOT / "data/evals/cases.json").read_bytes()
    rows = [json.loads(line) for line in (folder / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows == result["cases"]
    assert summarize(rows) == result["metrics"]
    baseline = None
    if label != "baseline":
        baseline = json.loads((runs / "baseline/results.json").read_text(encoding="utf-8"))
        assert compare_runs(baseline, result) == result["comparison"]
    assert render_markdown(result, baseline) == (folder / "report.md").read_text(encoding="utf-8")
