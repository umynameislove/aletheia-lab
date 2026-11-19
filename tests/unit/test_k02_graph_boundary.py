"""Bounded graph projections preserve canonical input and bind every continuation."""
from __future__ import annotations

import asyncio
import copy
import json
from urllib.parse import urlencode

import pytest
from test_k02_durable_exports import Harness
from test_k04_catalog_http import BASE, RESULT, _view

from aletheia_lab.hosted.catalog_http import _graph_page
from aletheia_lab.product.view import ProductGraph, ProductView

PATH = BASE + "/results/" + RESULT + "/graph"


def _large_view(count=120, *, dense=False):
    data = _view().model_dump(mode="json")
    evidence = data["evidence"][0]
    snapshot = next(n["id"] for n in data["graph"]["nodes"] if n["kind"] == "Snapshot")
    for number in range(count):
        item = {**evidence, "id": f"evidence-expanded-{number}", "kind": "snapshot",
            "reproduction_ref": {"record_id": data["snapshot"]["id"], "record_kind": "snapshot"}}
        data["evidence"].append(item)
        node = f"node-expanded-{number}"
        data["graph"]["nodes"].append({"id": node, "kind": "EvidenceItem", "source_id": item["id"], "visibility": "diagnosis"})
        data["graph"]["edges"].append({"id": f"edge-expanded-{number}", "kind": "OBSERVED_IN", "source": node,
                                         "target": snapshot, "visibility": "diagnosis"})
        for turn in data["conversation"]["turns"]:
            turn["visible_evidence_ids"].append(item["id"])
    if dense:
        claim = copy.deepcopy(data["claims"][0])
        data["claims"] = []
        data["graph"]["nodes"] = [n for n in data["graph"]["nodes"] if n["kind"] != "AtomicClaim"]
        data["graph"]["edges"] = [e for e in data["graph"]["edges"] if e["kind"] == "OBSERVED_IN"]
        disposition = next(n["id"] for n in data["graph"]["nodes"] if n["kind"] == "Disposition")
        citations = {n["source_id"]: n["id"] for n in data["graph"]["nodes"] if n["kind"] == "EvidenceItem"}
        for number in range(35):
            item = {**claim, "id": f"claim-dense-{number}", "citation_ids": list(citations), "counterevidence_ids": []}
            data["claims"].append(item)
            node = f"node-dense-{number}"
            data["graph"]["nodes"].append({"id": node, "kind": "AtomicClaim", "source_id": item["id"], "visibility": "diagnosis"})
            for index, target in enumerate(citations.values()):
                data["graph"]["edges"].append({"id": f"edge-dense-{number}-{index}", "kind": "CITES", "source": node,
                                                 "target": target, "visibility": "diagnosis"})
            data["graph"]["edges"].append({"id": f"edge-disposition-{number}", "kind": "ASSIGNED_DISPOSITION", "source": node,
                                             "target": disposition, "visibility": "diagnosis"})
        data["result"]["denominators"]["claims"] = len(data["claims"])
    return ProductView.model_validate_json(json.dumps(data))


async def _walk(h, limit=None):
    pages, cursor = [], None
    for _page in range(500):
        query = {} if limit is None else {"limit": limit}
        if cursor is not None:
            query["cursor"] = cursor
        messages, failure = await h.request(PATH, query=urlencode(query).encode())
        assert failure is None and messages[0]["status"] == 200, messages
        value = json.loads(messages[1]["body"])
        graph = ProductGraph.model_validate_json(json.dumps(value["graph"]))
        assert (b"cache-control", b"no-store") in messages[0]["headers"]
        pages.append((value, graph))
        cursor = value["next_cursor"]
        if cursor is None:
            return pages
    pytest.fail("Graph cursor did not make bounded forward progress")


def test_real_pages_walk_every_node_and_cross_page_edge_without_mutating_canonical_graph(tmp_path):
    async def run():
        h = Harness(tmp_path)
        h.view = _large_view()
        before = h.view.model_dump_json()
        nodes, edges = set(), set()
        for value, graph in await _walk(h):
            ids = {node.id for node in graph.nodes}
            assert len(ids) <= 50 and len(graph.edges) <= 1000
            assert all(edge.source in ids and edge.target in ids for edge in graph.edges)
            assert value["truncated"] is True
            nodes.update(ids)
            edges.update(edge.id for edge in graph.edges)
        assert nodes == {node.id for node in h.view.graph.nodes}
        assert edges == {edge.id for edge in h.view.graph.edges}
        assert h.view.model_dump_json() == before and h.renderer.calls == 0
    asyncio.run(run())


def test_dense_graph_walk_is_lossless_and_small_graph_retains_complete_single_response(tmp_path):
    async def run():
        h = Harness(tmp_path)
        pages = await _walk(h, 100)
        assert len(pages) == 1 and pages[0][0]["truncated"] is False
        h.view = _large_view(35, dense=True)
        pages = await _walk(h, 100)
        assert len(pages) >= 3 and pages[-1][0]["next_cursor"] is None
        assert all(len(graph.nodes) <= 100 and len(graph.edges) <= 1000 for _, graph in pages)
        assert all(value["truncated"] for value, _ in pages)
        assert {e.id for _, graph in pages for e in graph.edges} == {e.id for e in h.view.graph.edges}
        assert any(len(graph.edges) == 1000 for _, graph in pages)
    asyncio.run(run())


def test_limit_one_retains_cross_node_edges_with_two_endpoint_context_nodes(tmp_path):
    async def run():
        h = Harness(tmp_path)
        pages = await _walk(h, 1)
        assert all(len(graph.nodes) <= 2 and len(graph.edges) <= 1000 for _, graph in pages)
        assert {node.id for _, graph in pages for node in graph.nodes} == {node.id for node in h.view.graph.nodes}
        assert {edge.id for _, graph in pages for edge in graph.edges} == {edge.id for edge in h.view.graph.edges}
        assert any(len(graph.nodes) == 2 and graph.edges for _, graph in pages)
        assert sum(len(graph.nodes) for _, graph in pages) > len(h.view.graph.nodes)
    asyncio.run(run())


def test_edge_packer_continues_1001_edges_between_the_same_two_context_nodes():
    view = _view()
    nodes = view.graph.nodes[:2]
    edges = tuple(view.graph.edges[0].model_copy(update={"id": f"dense-edge-{number:04}",
        "source": nodes[0].id, "target": nodes[1].id}) for number in range(1001))
    source = ProductGraph(nodes=nodes, edges=edges)
    first, cursor = _graph_page(source, "edges", "", 1)
    assert len(first.nodes) == 2 and len(first.edges) == 1000 and cursor is not None
    last, continuation = _graph_page(source, cursor["phase"], cursor["after"], 1)
    assert len(last.nodes) == 2 and len(last.edges) == 1 and continuation is None
    assert {edge.id for edge in (*first.edges, *last.edges)} == {edge.id for edge in source.edges}


@pytest.mark.parametrize("change", ["user", "context", "revision", "limit", "graph"])
def test_cursor_binds_actor_context_membership_query_and_graph_identity(tmp_path, change):
    async def run():
        h = Harness(tmp_path)
        h.view = _large_view()
        first, _ = await h.request(PATH, query=b"limit=10")
        cursor = json.loads(first[1]["body"])["next_cursor"]
        limit = 10
        if change == "user":
            h.state.user = "other-user"
        elif change == "context":
            h.state.context = "other-context"
        elif change == "revision":
            h.state.revision += 1
        elif change == "limit":
            limit = 11
        else:
            h.view = _large_view(121)
        messages, failure = await h.request(PATH, query=urlencode({"limit": limit, "cursor": cursor}).encode())
        assert failure is None and messages[0]["status"] == 400
        assert json.loads(messages[1]["body"])["code"] == "cursor_invalid"
    asyncio.run(run())


@pytest.mark.parametrize("query", [b"limit=0", b"limit=101", b"limit=01", b"limit=2&limit=3", b"cursor=invalid"])
def test_invalid_graph_query_cannot_expose_an_unbounded_graph(tmp_path, query):
    async def run():
        h = Harness(tmp_path)
        h.view = _large_view()
        messages, failure = await h.request(PATH, query=query)
        assert failure is None and messages[0]["status"] == 400
        assert b"nodes" not in messages[1]["body"]
    asyncio.run(run())


def test_current_revocation_and_foreign_scope_precede_graph_cursor(tmp_path):
    async def run():
        h = Harness(tmp_path)
        h.view = _large_view()
        first, _ = await h.request(PATH, query=b"limit=1")
        query = urlencode({"limit": 1, "cursor": json.loads(first[1]["body"])["next_cursor"]}).encode()
        for path in [PATH.replace("ws1", "ws2"), PATH.replace(RESULT, "foreign-result")]:
            messages, _ = await h.request(path, query=query)
            assert messages[0]["status"] == 404
        h.state.allowed = False
        messages, _ = await h.request(PATH, query=query)
        assert messages[0]["status"] == 404 and b"nodes" not in messages[1]["body"]
    asyncio.run(run())
