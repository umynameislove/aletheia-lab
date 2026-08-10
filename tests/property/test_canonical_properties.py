"""Property tests — Group A: canonical bytes and identity.

Covers §2.2 properties 1–10:
  1. Dict insertion order → same canonical bytes and SHA-256
  2. Unicode NFC round-trip, byte-identical
  3. NaN, Infinity, -Infinity rejected at any depth
  4. Single semantic field mutation → digest changes
  5. Metadata excluded by contract does not affect identity
  6. Every semantic field always affects identity
  7. Order-sensitive list permutation → digest changes
  8. Collection canonicalization independent of dict iteration order
  9. P1/P2 domain prefixes cannot collide
  10. canonical_sha256 output always lowercase 64-char hex
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata

import pytest
from hypothesis import assume, example, given
from hypothesis import strategies as st
from pydantic import ValidationError

from aletheia_lab.benchmark.p2.canonical import (
    canonical_bytes,
    canonical_sha256,
)
from aletheia_lab.benchmark.p2.identity import (
    IDENTITY_FIELD_NAMES,
    P2_CANDIDATE_PREFIX,
    P2_FAMILY_PREFIX,
    DataDriftParameters,
    FamilyIdentity,
    candidate_id_for,
    family_id_for,
    proposed_family_sha256,
)

# ---------------------------------------------------------------------------
# Constants — module-level for performance
# ---------------------------------------------------------------------------

_HEX_CHARS = "0123456789abcdef"
_ASCII_ALPHA = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_SAFE_ID_CHARS = _ASCII_ALPHA + "0123456789-_"

# Independent oracle for property A1 — computed WITHOUT calling canonical_bytes.
#
# payload = {"b": "foo", "a": "bar"}
# Step 1 — sort keys:           {"a": "bar", "b": "foo"}
# Step 2 — compact JSON:        '{"a":"bar","b":"foo"}'
# Step 3 — UTF-8 encode:        b'{"a":"bar","b":"foo"}'
# Step 4 — SHA-256:             hashlib.sha256(b'...').hexdigest()
#
# This verifies the production output against a reference built from first principles,
# satisfying §2.2: "phải dựng ít nhất một expected canonical byte string thủ công".
_A1_ORACLE_BYTES: bytes = b'{"a":"bar","b":"foo"}'
_A1_ORACLE_SHA256: str = hashlib.sha256(_A1_ORACLE_BYTES).hexdigest()

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


@st.composite
def sha256_digests(draw):  # type: ignore[no-untyped-def]
    """Valid lowercase 64-char SHA-256 hex digest."""
    return draw(st.text(alphabet=_HEX_CHARS, min_size=64, max_size=64))


@st.composite
def safe_identifiers(draw, *, min_size=1, max_size=32):  # type: ignore[no-untyped-def]
    """ASCII alphanumeric identifier; not a P1-namespace ('p1-') string."""
    s = draw(st.text(alphabet=_SAFE_ID_CHARS, min_size=min_size, max_size=max_size))
    assume(not s.lower().startswith("p1-"))
    return s


@st.composite
def drift_parameters(draw):  # type: ignore[no-untyped-def]
    """Valid DataDriftParameters for use in FamilyIdentity strategies."""
    feature = draw(safe_identifiers(min_size=1, max_size=20))
    weight = draw(
        st.floats(
            min_value=0.01,
            max_value=1.0,
            allow_nan=False,
            allow_infinity=False,
        )
    )
    size = draw(st.integers(min_value=1, max_value=500))
    return DataDriftParameters(
        feature=feature,
        target_distribution={feature: weight},
        output_size=size,
    )


@st.composite
def valid_slot_ids(draw):  # type: ignore[no-untyped-def]
    """Valid P2 slot ID matching SLOT_ID_PATTERN ^M[123]-(?:F|S|I|B|R)[0-9]{1,2}$."""
    m = draw(st.sampled_from(["M1", "M2", "M3"]))
    kind = draw(st.sampled_from(["F", "S", "I", "B", "R"]))
    n = draw(st.integers(min_value=1, max_value=9))
    return f"{m}-{kind}{n}"


@st.composite
def family_identities(draw):  # type: ignore[no-untyped-def]
    """Valid FamilyIdentity with data_drift parameters."""
    return FamilyIdentity(
        dataset_snapshot_id=draw(safe_identifiers(min_size=2, max_size=32)),
        dataset_sha256=draw(sha256_digests()),
        model_data_split_manifest_sha256=draw(sha256_digests()),
        fault_type="data_drift",
        intervention_type=draw(safe_identifiers(min_size=1, max_size=32)),
        canonical_intervention_parameters=draw(drift_parameters()),
        seed=draw(st.integers(min_value=0, max_value=100_000)),
        reference_construction_id=draw(safe_identifiers(min_size=1, max_size=32)),
        injector_contract_version=draw(safe_identifiers(min_size=1, max_size=32)),
        model_specification_sha256=draw(sha256_digests()),
        preprocessing_specification_sha256=draw(sha256_digests()),
        identity_schema_version="p2-family-identity/v1",
    )


# ---------------------------------------------------------------------------
# Property A1 — insertion order invariance
# ---------------------------------------------------------------------------


def test_a1_manual_oracle_canonical_bytes() -> None:
    """Independent-oracle verification: canonical bytes for {"b":"foo","a":"bar"}.

    Required by §2.2: 'Test phải dựng ít nhất một expected canonical
    byte string thủ công. Không chỉ kiểm tra hash(a)==hash(b) bằng cùng một
    helper production.'

    The expected bytes and SHA-256 are computed at module load (see _A1_ORACLE_*
    constants above) without calling canonical_bytes or canonical_sha256.
    """
    payload = {"b": "foo", "a": "bar"}
    assert canonical_bytes(payload) == _A1_ORACLE_BYTES
    assert canonical_sha256(payload) == _A1_ORACLE_SHA256


@given(
    k1=st.text(alphabet=_ASCII_ALPHA, min_size=1, max_size=8),
    k2=st.text(alphabet=_ASCII_ALPHA, min_size=1, max_size=8),
    v1=st.text(alphabet=_ASCII_ALPHA, min_size=1, max_size=8),
    v2=st.text(alphabet=_ASCII_ALPHA, min_size=1, max_size=8),
)
@example(k1="b", k2="a", v1="foo", v2="bar")  # classic reverse-alphabetical boundary
def test_a1_insertion_order_invariant(k1: str, k2: str, v1: str, v2: str) -> None:
    """Dict with same K→V mapping but different insertion order gives identical
    canonical bytes and SHA-256 (property 1).
    """
    assume(k1 != k2)

    payload_fwd: dict[str, str] = {k1: v1, k2: v2}
    payload_rev: dict[str, str] = {k2: v2, k1: v1}

    assert canonical_bytes(payload_fwd) == canonical_bytes(payload_rev)
    assert canonical_sha256(payload_fwd) == canonical_sha256(payload_rev)


# ---------------------------------------------------------------------------
# Property A2 — Unicode NFC round-trip, byte-identical
# ---------------------------------------------------------------------------


@given(
    s=st.text(),
    form=st.sampled_from(["NFC", "NFD", "NFKC", "NFKD"]),
)
@example(s="caf\u00e9", form="NFC")     # café already in NFC
@example(s="cafe\u0301", form="NFD")    # café in NFD — must produce same bytes
def test_a2_nfc_equivalent_strings_are_byte_identical(s: str, form: str) -> None:
    """Strings that NFC-normalize to the same value produce identical canonical
    bytes (property 2 — metamorphic property).
    """
    normalized = unicodedata.normalize(form, s)
    assume(unicodedata.normalize("NFC", s) == unicodedata.normalize("NFC", normalized))

    assert canonical_bytes(s) == canonical_bytes(normalized)


@given(
    text_value=st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N", "P", "S", "Zs")
        )
    )
)
@example(text_value="caf\u00e9")
@example(text_value="hello world")
def test_a2_canonical_bytes_matches_nfc_oracle(text_value: str) -> None:
    """canonical_bytes on a string matches the independent NFC oracle (property 2).

    Oracle (no call to canonical_bytes):
      1. NFC-normalize the input
      2. json.dumps with compact separators and ensure_ascii=False
      3. encode UTF-8
    """
    nfc = unicodedata.normalize("NFC", text_value)
    # json.dumps wraps a string in quotes; canonical_bytes does the same via _canonicalize
    expected = json.dumps(nfc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert canonical_bytes(text_value) == expected


# ---------------------------------------------------------------------------
# Property A3 — NaN / Infinity / -Infinity rejected at any depth
# ---------------------------------------------------------------------------


@given(
    non_finite=st.sampled_from([float("nan"), float("inf"), float("-inf")]),
    depth=st.integers(min_value=0, max_value=3),
    key=st.text(alphabet=_ASCII_ALPHA, min_size=1, max_size=8),
)
@example(non_finite=float("nan"), depth=0, key="x")    # top-level float
@example(non_finite=float("inf"), depth=1, key="v")    # nested in dict
@example(non_finite=float("-inf"), depth=3, key="d")   # deeply nested
def test_a3_nonfinite_rejected_at_any_depth(
    non_finite: float, depth: int, key: str
) -> None:
    """NaN, Infinity, and -Infinity are rejected at every nesting depth (property 3)."""
    payload: object = non_finite
    for _ in range(depth):
        payload = {key: payload}

    with pytest.raises(ValueError, match="non-finite"):
        canonical_bytes(payload)


# ---------------------------------------------------------------------------
# Property A4 — single field mutation changes digest
# ---------------------------------------------------------------------------


@given(
    payload=st.fixed_dictionaries(
        {
            "field_a": st.text(alphabet=_ASCII_ALPHA, min_size=1, max_size=16),
            "field_b": st.text(alphabet=_ASCII_ALPHA, min_size=1, max_size=16),
            "field_c": st.text(alphabet=_ASCII_ALPHA, min_size=1, max_size=16),
        }
    ),
    new_value=st.text(alphabet=_ASCII_ALPHA, min_size=1, max_size=16),
)
@example(
    payload={"field_a": "original", "field_b": "unchanged", "field_c": "same"},
    new_value="modified",
)
def test_a4_single_field_mutation_changes_digest(
    payload: dict[str, str], new_value: str
) -> None:
    """Replacing exactly one field value changes the SHA-256 digest (property 4)."""
    assume(new_value != payload["field_a"])

    mutated: dict[str, str] = {**payload, "field_a": new_value}

    assert canonical_sha256(payload) != canonical_sha256(mutated)


# ---------------------------------------------------------------------------
# Property A5 — metadata excluded by contract does not affect identity
# ---------------------------------------------------------------------------


@given(identity=family_identities())
def test_a5_identity_payload_has_exactly_twelve_fields(
    identity: FamilyIdentity,
) -> None:
    """identity_payload() returns exactly the twelve semantic fields in
    IDENTITY_FIELD_NAMES; no extra field can enter the hash (property 5).

    proposed_family_sha256 must equal canonical_sha256(identity_payload()),
    proving no hidden field contributes to identity.
    """
    payload = identity.identity_payload()

    assert set(payload.keys()) == set(IDENTITY_FIELD_NAMES)
    assert len(payload) == len(IDENTITY_FIELD_NAMES)

    expected_hash = canonical_sha256(payload)
    assert proposed_family_sha256(identity) == expected_hash


# ---------------------------------------------------------------------------
# Property A6 — every semantic field always affects identity
# ---------------------------------------------------------------------------


def test_a6_all_identity_fields_affect_hash() -> None:
    """Every field in IDENTITY_FIELD_NAMES changes the hash when mutated (property 6).

    Uses fixed known values and tests each of the twelve fields exactly once.
    Oracle: canonical_sha256 on raw payload dicts — independent of
    FamilyIdentity construction, proving each field genuinely participates.
    """
    base_params = DataDriftParameters(
        feature="Contract",
        target_distribution={"Contract": 0.8},
        output_size=100,
    )
    base_identity = FamilyIdentity(
        dataset_snapshot_id="telco-v1",
        dataset_sha256="a" * 64,
        model_data_split_manifest_sha256="b" * 64,
        fault_type="data_drift",
        intervention_type="categorical_distribution_shift",
        canonical_intervention_parameters=base_params,
        seed=42,
        reference_construction_id="train-split-v1",
        injector_contract_version="drift-injector-v1",
        model_specification_sha256="c" * 64,
        preprocessing_specification_sha256="d" * 64,
        identity_schema_version="p2-family-identity/v1",
    )
    base_payload = base_identity.identity_payload()
    base_hash = canonical_sha256(base_payload)

    alt_params = DataDriftParameters(
        feature="Tenure",
        target_distribution={"Tenure": 0.5},
        output_size=200,
    )
    # One distinct mutation per field — all different from the base value
    mutations: dict[str, object] = {
        "dataset_snapshot_id": "telco-v2",
        "dataset_sha256": "1" * 64,
        "model_data_split_manifest_sha256": "2" * 64,
        "fault_type": "label_noise",
        "intervention_type": "label_corruption",
        "canonical_intervention_parameters": alt_params.model_dump(mode="json"),
        "seed": 99,
        "reference_construction_id": "train-split-v2",
        "injector_contract_version": "drift-injector-v2",
        "model_specification_sha256": "3" * 64,
        "preprocessing_specification_sha256": "4" * 64,
        "identity_schema_version": "p2-family-identity/v2",
    }

    assert set(mutations.keys()) == set(IDENTITY_FIELD_NAMES), (
        "Mutation table must cover exactly all twelve identity fields"
    )

    for field_name in IDENTITY_FIELD_NAMES:
        mutated_payload = {**base_payload, field_name: mutations[field_name]}
        mutated_hash = canonical_sha256(mutated_payload)
        assert mutated_hash != base_hash, (
            f"Field {field_name!r} did not change the identity hash when mutated"
        )


@given(
    identity=family_identities(),
    new_seed=st.integers(min_value=0, max_value=100_000),
)
def test_a6_seed_field_affects_identity_generated(
    identity: FamilyIdentity, new_seed: int
) -> None:
    """Hypothesis-verified: changing seed always changes the identity hash (property 6)."""
    assume(new_seed != identity.seed)

    original = identity.identity_payload()
    mutated = {**original, "seed": new_seed}

    assert canonical_sha256(original) != canonical_sha256(mutated)


@given(
    identity=family_identities(),
    new_sha=sha256_digests(),
)
def test_a6_dataset_sha256_affects_identity_generated(
    identity: FamilyIdentity, new_sha: str
) -> None:
    """Hypothesis-verified: changing dataset_sha256 always changes the hash (property 6)."""
    assume(new_sha != identity.dataset_sha256)

    original = identity.identity_payload()
    mutated = {**original, "dataset_sha256": new_sha}

    assert canonical_sha256(original) != canonical_sha256(mutated)


# ---------------------------------------------------------------------------
# Property A7 — order-sensitive list: permutation changes digest
# ---------------------------------------------------------------------------


@given(
    items=st.lists(
        st.text(alphabet=_ASCII_ALPHA, min_size=1, max_size=8),
        min_size=2,
        max_size=6,
        unique=True,
    )
)
@example(items=["b", "a"])
@example(items=["z", "a", "m"])
def test_a7_list_order_sensitive(items: list[str]) -> None:
    """An order-sensitive list that is reversed produces a different digest (property 7).

    Callers needing order-insensitive semantics must sort before serializing.
    """
    reversed_items = list(reversed(items))
    assume(items != reversed_items)

    assert canonical_sha256(items) != canonical_sha256(reversed_items)


# ---------------------------------------------------------------------------
# Property A8 — dict canonicalization independent of iteration order
# ---------------------------------------------------------------------------


@given(
    payload=st.dictionaries(
        keys=st.text(alphabet=_ASCII_ALPHA, min_size=1, max_size=8),
        values=st.text(alphabet=_ASCII_ALPHA, min_size=1, max_size=8),
        min_size=2,
        max_size=6,
    )
)
@example(payload={"z": "last", "a": "first", "m": "middle"})
def test_a8_dict_canonicalization_independent_of_iteration(
    payload: dict[str, str],
) -> None:
    """Dict canonical bytes are independent of insertion/iteration order (property 8).

    Also verifies against an independent oracle: manually sort keys and serialize
    without calling canonical_bytes.
    """
    reversed_payload = dict(sorted(payload.items(), reverse=True))

    assert canonical_bytes(payload) == canonical_bytes(reversed_payload)

    # Independent oracle: sort keys, compact JSON, UTF-8 encode
    expected = json.dumps(
        dict(sorted(payload.items())),
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    assert canonical_bytes(payload) == expected


# ---------------------------------------------------------------------------
# Property A9 — P1/P2 domain prefix no collision
# ---------------------------------------------------------------------------


@given(identity=family_identities())
def test_a9_family_id_always_p2_prefixed(identity: FamilyIdentity) -> None:
    """family_id_for() always produces a 'p2-family-' prefixed ID (property 9)."""
    fid = family_id_for(identity)

    assert fid.startswith(P2_FAMILY_PREFIX)  # "p2-family-"
    assert not fid.startswith("p1-")
    assert _SHA256_RE.fullmatch(fid[len(P2_FAMILY_PREFIX) :]) is not None


@given(sha=sha256_digests(), slot=valid_slot_ids())
def test_a9_candidate_id_always_p2_prefixed(sha: str, slot: str) -> None:
    """candidate_id_for() always produces a 'p2-candidate-' prefixed ID (property 9)."""
    cid = candidate_id_for(slot_id=slot, family_fingerprint=sha)

    assert cid.startswith(P2_CANDIDATE_PREFIX)  # "p2-candidate-"
    assert not cid.startswith("p1-")
    assert not cid.startswith("p2-family-")


def test_a9_p1_snapshot_id_rejected_by_identity() -> None:
    """FamilyIdentity rejects 'p1-' prefixed dataset_snapshot_id (property 9).

    Fixed @example verifying domain-separation: a P1-namespaced identifier
    must not seed a Phase 2 identity.
    """
    params = DataDriftParameters(
        feature="Contract",
        target_distribution={"Contract": 0.8},
        output_size=100,
    )
    with pytest.raises(ValidationError, match="Phase 1"):
        FamilyIdentity(
            dataset_snapshot_id="p1-telco-dataset",  # P1 namespace — must be rejected
            dataset_sha256="a" * 64,
            model_data_split_manifest_sha256="b" * 64,
            fault_type="data_drift",
            intervention_type="categorical_distribution_shift",
            canonical_intervention_parameters=params,
            seed=0,
            reference_construction_id="ref-v1",
            injector_contract_version="injector-v1",
            model_specification_sha256="c" * 64,
            preprocessing_specification_sha256="d" * 64,
            identity_schema_version="p2-family-identity/v1",
        )


# ---------------------------------------------------------------------------
# Property A10 — SHA-256 output always lowercase 64-char hex
# ---------------------------------------------------------------------------


@given(
    payload=st.one_of(
        st.text(alphabet=_ASCII_ALPHA, min_size=1, max_size=32),
        st.fixed_dictionaries(
            {"k": st.text(alphabet=_ASCII_ALPHA, min_size=1, max_size=8)}
        ),
        st.lists(
            st.text(alphabet=_ASCII_ALPHA, min_size=1, max_size=8),
            min_size=1,
            max_size=5,
        ),
    )
)
@example(payload={"k": "value"})
@example(payload="hello")
@example(payload=["a", "b", "c"])
def test_a10_sha256_output_is_lowercase_64_hex(payload: object) -> None:
    """canonical_sha256 always returns a lowercase 64-char hex string (property 10)."""
    result = canonical_sha256(payload)

    assert isinstance(result, str)
    assert len(result) == 64
    assert _SHA256_RE.fullmatch(result) is not None
