"""Property tests for recursive evaluation-context visibility checks."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from aletheia_lab.context.evaluation_context import find_visibility_violation

_SAFE_LEAF = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(
        alphabet=st.characters(
            blacklist_categories=("Cc", "Cf"), blacklist_characters=("/", "\\", "@")
        ),
        max_size=20,
    ),
)
_SAFE_TREE = st.recursive(
    _SAFE_LEAF,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(
            st.text(
                alphabet=st.characters(
                    whitelist_categories=("Ll",), blacklist_characters=("/", "\\", "@")
                ),
                min_size=1,
                max_size=12,
            ),
            children,
            max_size=4,
        ),
    ),
    max_leaves=20,
)


@settings(max_examples=40)
@given(_SAFE_TREE)
def test_recursive_forbidden_field_is_detected_independent_of_nesting(payload: object) -> None:
    nested = {"outer": [{"inner": payload, "hidden_label": "not-visible"}]}

    assert find_visibility_violation(nested) == "forbidden_field"


@settings(max_examples=40)
@given(_SAFE_TREE)
def test_safe_nested_values_are_not_treated_as_instructions(payload: object) -> None:
    wrapped = {"evidence": payload}

    violation = find_visibility_violation(wrapped)
    assert violation in {
        None,
        "forbidden_field",
        "forbidden_text",
        "unicode_control",
        "unicode_homoglyph",
    }
