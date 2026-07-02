from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(
    reason="Governance milter was removed with MTA support; inline policy checks are a follow-up task."
)


def test_governance_milter_removed() -> None:
    pass
