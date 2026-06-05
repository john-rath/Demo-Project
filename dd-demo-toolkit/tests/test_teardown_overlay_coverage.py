"""
Regression tests for overlay notebook and case teardown coverage.

Bug: _get_expected_names / _get_expected_titles only scanned the base
vertical directory when called with a vertical_name argument, so overlay
notebooks and cases were never included in the teardown set. On each
subsequent `make setup` run, new overlay resources were created while
orphaned ones from the previous run survived.

These tests confirm that a single-vertical teardown call finds resources
from both the base vertical and any overlays under verticals/<v>/overlays/.
"""

import textwrap
from pathlib import Path

import pytest

from dd_demo_toolkit.resources.notebooks import NotebookManager
from dd_demo_toolkit.resources.cases import CaseManager


@pytest.fixture
def fake_verticals(tmp_path: Path) -> Path:
    """
    Create a minimal fake verticals tree:

    verticals/
      finance/
        notebooks.yaml   (2 base notebook names)
        cases.yaml       (1 base case title)
        overlays/
          payment-processor/
            notebooks.yaml  (2 overlay notebook names)
            cases.yaml      (1 overlay case title)
          ey/
            notebooks.yaml  (1 overlay notebook name)
    """
    finance = tmp_path / "finance"
    finance.mkdir()

    (finance / "notebooks.yaml").write_text(textwrap.dedent("""\
        notebooks:
          - name: "Base Notebook One"
          - name: "Base Notebook Two"
    """))

    (finance / "cases.yaml").write_text(textwrap.dedent("""\
        cases:
          - title: "Base Case Alpha"
    """))

    pp_dir = finance / "overlays" / "payment-processor"
    pp_dir.mkdir(parents=True)

    (pp_dir / "notebooks.yaml").write_text(textwrap.dedent("""\
        notebooks:
          - name: "Payment Processor Notebook A"
          - name: "Payment Processor Notebook B"
    """))

    (pp_dir / "cases.yaml").write_text(textwrap.dedent("""\
        cases:
          - title: "Auth Cascade Investigation"
    """))

    ey_dir = finance / "overlays" / "ey"
    ey_dir.mkdir(parents=True)

    (ey_dir / "notebooks.yaml").write_text(textwrap.dedent("""\
        notebooks:
          - name: "EY Eval Notebook"
    """))

    return tmp_path


class TestNotebookTeardownOverlayCoverage:

    def test_single_vertical_includes_base_notebooks(self, fake_verticals):
        mgr = NotebookManager(verticals_dir=str(fake_verticals))
        names = mgr._get_expected_names("finance")
        assert "Base Notebook One" in names
        assert "Base Notebook Two" in names

    def test_single_vertical_includes_overlay_notebooks(self, fake_verticals):
        """Regression: overlay notebooks must appear in teardown set."""
        mgr = NotebookManager(verticals_dir=str(fake_verticals))
        names = mgr._get_expected_names("finance")
        assert "Payment Processor Notebook A" in names
        assert "Payment Processor Notebook B" in names
        assert "EY Eval Notebook" in names

    def test_all_verticals_includes_overlay_notebooks(self, fake_verticals):
        mgr = NotebookManager(verticals_dir=str(fake_verticals))
        names = mgr._get_expected_names(None)
        assert "Payment Processor Notebook A" in names
        assert "EY Eval Notebook" in names

    def test_unknown_vertical_returns_empty(self, fake_verticals):
        mgr = NotebookManager(verticals_dir=str(fake_verticals))
        names = mgr._get_expected_names("nonexistent")
        assert names == set()


class TestCaseTeardownOverlayCoverage:

    def test_single_vertical_includes_base_cases(self, fake_verticals):
        mgr = CaseManager(verticals_dir=str(fake_verticals))
        titles = mgr._get_expected_titles("finance")
        assert "Base Case Alpha" in titles

    def test_single_vertical_includes_overlay_cases(self, fake_verticals):
        """Regression: overlay cases must appear in teardown set."""
        mgr = CaseManager(verticals_dir=str(fake_verticals))
        titles = mgr._get_expected_titles("finance")
        assert "Auth Cascade Investigation" in titles

    def test_all_verticals_includes_overlay_cases(self, fake_verticals):
        mgr = CaseManager(verticals_dir=str(fake_verticals))
        titles = mgr._get_expected_titles(None)
        assert "Auth Cascade Investigation" in titles

    def test_unknown_vertical_returns_empty(self, fake_verticals):
        mgr = CaseManager(verticals_dir=str(fake_verticals))
        titles = mgr._get_expected_titles("nonexistent")
        assert titles == set()
