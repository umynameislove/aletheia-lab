"""Executable proposal for peer review; never a production v3 or primary certificate."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sys
import time
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
from pathlib import Path

POLICY = "metric-window-decimal/v1-proposal"
FIELDS = ("run", "metric", "context", "class", "unit", "value")
WINDOW = 10


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def decimal_value(text: str) -> Decimal | None:
    if not isinstance(text, str) or len(text) > 256 or re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", text) is None:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if not value.is_finite() or abs(value.as_tuple().exponent) > 256 or value.adjusted() > 256:
        return None
    return value


def decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    text = text.rstrip("0").rstrip(".") if "." in text else text
    return "0" if value == 0 else text


def aggregate(source: bytes, *, source_name: str, snapshot_id: str) -> dict[str, object]:
    """Hash original bytes and fixed canonical CSV row dictionaries; no float conversion."""
    if not 0 < len(source) <= 32 * 1024**2:
        raise ValueError("Bounded source bytes required")
    if not source_name or len(source_name) > 255 or any(ord(c) < 32 for c in source_name):
        raise ValueError("Bounded display name required")
    reader = csv.DictReader(io.StringIO(source.decode("utf-8-sig"), newline=""))
    if tuple(reader.fieldnames or ()) != FIELDS:
        raise ValueError("Exact explicit source fields required")
    rows = list(reader)
    if not rows or len(rows) > 100000 or any(None in row or any(v is None or len(v) > 512 for v in row.values()) for row in rows):
        raise ValueError("Bounded complete source records required")
    source_sha = hashlib.sha256(source).hexdigest()
    item_id = "item-" + digest({"source": source_sha, "name": source_name})
    claims, evidence, abstentions = [], [], []
    for start in range(0, len(rows), WINDOW):
        window = rows[start:start + WINDOW]
        stop = start + len(window)
        scopes = {tuple(row[field] for field in FIELDS[:-1]) for row in window}
        numbers = [number for row in window if (number := decimal_value(row["value"])) is not None]
        reason = "mixed_series_window" if len(scopes) != 1 else "no_valid_numeric_values" if not numbers else None
        mean = None
        if reason is None:
            with localcontext() as context:
                context.prec = 1100
                total = sum(numbers, Decimal(0))
                # A decimal string cannot express a recurring fraction exactly.
                divisor = (Fraction(total) / len(numbers)).denominator
                for factor in (2, 5):
                    while divisor % factor == 0:
                        divisor //= factor
                if divisor != 1:
                    reason = "mean_has_no_finite_decimal_representation"
                else:
                    mean = total / Decimal(len(numbers))
        if reason is not None:
            abstentions.append({"record_start": start, "record_stop": stop, "reason": reason})
            continue
        run, metric, scope, label, unit = next(iter(scopes))
        row_sha = digest(window)
        identity = {"policy": POLICY, "snapshot": snapshot_id, "item": item_id,
                    "start": start, "stop": stop, "rows_sha256": row_sha}
        evidence_id = "evidence-" + digest(identity)
        stats = dict(count=len(numbers), minimum=decimal_text(min(numbers)), maximum=decimal_text(max(numbers)), mean=decimal_text(mean))
        text = f"Run {run}, metric {metric}: {stats['count']} numeric observations in source records [{start}, {stop}); minimum {stats['minimum']}, maximum {stats['maximum']}, mean {stats['mean']}."
        claim_id = "claim-" + digest({"window": identity, "statistics": stats})
        claims.append(dict(id=claim_id, text=text, support="fully_supported", epistemic_state="observed",
            citation_ids=[evidence_id], counterevidence_ids=[], metric=metric, run=run, context=scope or None,
            **{"class": label or None}, unit=unit or None, **stats))
        evidence.append(dict(id=evidence_id, kind="metric_window_observation", title=source_name, text=text,
            reproduction_ref=dict(record_id=snapshot_id, record_kind="snapshot"),
            source_sha256=source_sha, source_item_id=item_id, source_display_name=source_name,
            record_start=start, record_stop=stop, rows_sha256=row_sha, metric=metric, run=run,
            context=scope or None, **{"class": label or None}, unit=unit or None, visibility="diagnosis", redacted=False))
    nodes = [{"id": "node-snapshot", "kind": "Snapshot", "source_id": snapshot_id},
             {"id": "node-disposition", "kind": "Disposition", "source_id": "result-proposal"}]
    edges = []
    for claim, item in zip(claims, evidence, strict=True):
        cn, en = "node-" + claim["id"], "node-" + item["id"]
        nodes.extend([dict(id=cn, kind="AtomicClaim", source_id=claim["id"]), dict(id=en, kind="EvidenceItem", source_id=item["id"])])
        for kind, source_id, target in (("OBSERVED_IN", en, "node-snapshot"), ("CITES", cn, en), ("ASSIGNED_DISPOSITION", cn, "node-disposition")):
            edges.append(dict(id="edge-" + digest([kind, source_id, target]), kind=kind, source=source_id, target=target))
    return dict(schema_version="k17-metric-window-prototype/v1", intended_product_schema="p6-product-view/v3",
        production_capability=False, primary_dataset_qualified=False, aggregation_policy_version=POLICY,
        window_rows=WINDOW, source_row_count=len(rows), abstained_window_count=len(abstentions),
        source_sha256=source_sha, source_file=source_name, snapshot_id=snapshot_id,
        result=dict(disposition="abstain", causal_status="unverified", claim_count=len(claims)),
        claims=claims, evidence=evidence, graph=dict(nodes=nodes, edges=edges), abstentions=abstentions)


def source_csv(count: int) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(FIELDS)
    for index in range(count):
        writer.writerow(("synthetic_run", "loss", "synthetic_context", "", "ratio", f"0.{index % 100:02}"))
    return output.getvalue().encode("utf-8")


def self_check() -> None:
    view = aggregate(source_csv(20), source_name="metrics.csv", snapshot_id="snapshot-test")
    assert view["claims"][0]["mean"] == "0.045" and view["claims"][1]["mean"] == "0.145"
    assert (view["evidence"][0]["record_start"], view["evidence"][0]["record_stop"]) == (0, 10)
    assert len(view["graph"]["nodes"]) == 6 and len(view["graph"]["edges"]) == 6
    assert view == aggregate(source_csv(20), source_name="metrics.csv", snapshot_id="snapshot-test")
    assert decimal_value("true") is None and decimal_value("NaN") is None and decimal_value("1e999999") is None
    assert decimal_value("0.123456789123456789") == Decimal("0.123456789123456789")
    empty = source_csv(10).decode().splitlines()
    for index in range(1, len(empty)):
        empty[index] = empty[index].rsplit(",", 1)[0] + ","
    abstained = aggregate(("\n".join(empty)+"\n").encode(), source_name="metrics.csv", snapshot_id="s")
    assert not abstained["claims"] and abstained["abstained_window_count"] == 1
    for numerator in ("1", "2"):
        partial = empty.copy()
        for index, value in enumerate((numerator, "0", "0"), 1):
            partial[index] = partial[index].rsplit(",", 1)[0] + "," + value
        recurring = aggregate(("\n".join(partial)+"\n").encode(), source_name="metrics.csv", snapshot_id="s")
        assert recurring["abstentions"][0]["reason"] == "mean_has_no_finite_decimal_representation"
    mixed = source_csv(10).replace(b"synthetic_run", b"other_run", 1)
    assert aggregate(mixed, source_name="metrics.csv", snapshot_id="s")["abstentions"][0]["reason"] == "mixed_series_window"
    changed = aggregate(source_csv(20).replace(b"0.00", b"0.01", 1), source_name="metrics.csv", snapshot_id="snapshot-test")
    assert changed["source_sha256"] != view["source_sha256"] and changed["evidence"][0]["rows_sha256"] != view["evidence"][0]["rows_sha256"]
    assert not view["production_capability"] and not view["primary_dataset_qualified"]


def main() -> None:
    self_check()
    output = Path(sys.argv[1])
    output.mkdir(exist_ok=False)
    receipts = []
    for size, count in (("S", 1000), ("M", 10000), ("L", 100000)):
        source = source_csv(count)
        (output / f"{size}-metrics.csv").write_bytes(source)
        started = time.perf_counter()
        snapshot = "snapshot-" + hashlib.sha256(source).hexdigest()
        view = aggregate(source, source_name="metrics.csv", snapshot_id=snapshot)
        payload = encoded(view)
        assert len(view["claims"]) == count // 10 and not view["abstentions"]
        assert len(view["evidence"]) == count // 10
        assert len(view["graph"]["nodes"]) == 2 * (count // 10) + 2
        assert len(view["graph"]["edges"]) == 3 * (count // 10)
        assert len(payload) <= 32 * 1024**2
        node_ids = {node["id"] for node in view["graph"]["nodes"]}
        assert len(node_ids) == len(view["graph"]["nodes"])
        assert all(edge["source"] in node_ids and edge["target"] in node_ids for edge in view["graph"]["edges"])
        (output / f"{size}-prototype.json").write_bytes(payload)
        receipts.append(dict(size=size,source_rows=count,source_bytes=len(source),source_sha256=hashlib.sha256(source).hexdigest(),
            claims=len(view["claims"]),evidence=len(view["evidence"]),nodes=len(node_ids),edges=len(view["graph"]["edges"]),
            json_bytes=len(payload),json_sha256=hashlib.sha256(payload).hexdigest(),aggregation_and_encoding_seconds=time.perf_counter()-started,
            complete_json_within_32mib=True,primary_or_production_pass=False))
    receipt = dict(scope="Executable contract proposal and measured synthetic files; not the production producer, not seed cardinalities, not a capacity test.",
        policy=POLICY,self_checks_passed=True,fixtures=receipts,production_capability=False,primary_campaign_pass=False,
        mean_policy="Exact finite decimal only; recurring mean abstains explicitly. Decimal strings are never calculated from floats.",
        rows_hash_policy="SHA256 of sorted-key compact UTF8 JSON array of complete original CSV row dictionaries; source SHA256 pins original CSV bytes.",
        remaining="Approve claim/source/rounding/export policy; wire producer/parser/canonical storage/projections/worker/consumer/export in one version before primary seeding.")
    (output/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt,ensure_ascii=True))


if __name__ == "__main__":
    main()
