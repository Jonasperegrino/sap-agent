"""Guard the sap.m row-shape handling in the single-evaluate table reader."""

from __future__ import annotations

from sap_agent.tools.extract import _EXTRACT_ALL_SCRIPT


def test_fast_path_reads_data_cells_only_for_sap_m_rows() -> None:
    # sap.m.Table rows start with a header-less highlight cell; reading every
    # <td> shifted Status/Built by one column and broke all count_where answers.
    assert "td.sapMListTblCell" in _EXTRACT_ALL_SCRIPT
    # Other table types (no sap.m data cells) still fall back to every cell.
    assert """tr.querySelectorAll('td, [role="cell"]')""" in _EXTRACT_ALL_SCRIPT
