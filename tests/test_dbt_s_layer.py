"""Basic tests for dbt_s_layer."""

import dbt_s_layer


def test_import() -> None:
    """Package imports cleanly and exposes an __all__ list."""
    assert hasattr(dbt_s_layer, "__all__")
    assert dbt_s_layer.__all__ == []
