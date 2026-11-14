"""In-memory port for pre-existing HTTP dispatch tests; SQL is tested separately."""
from __future__ import annotations

import json

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.catalog_http import _graph_page
from aletheia_lab.hosted.cursors import CursorInvalid
from aletheia_lab.hosted.result_views import prepare_projection


class MemoryViews:
    def __init__(self, get_view, authorize):
        self.get_view, self.authorize = get_view, authorize

    async def manifest(self, digest, workspace, result):
        await self.authorize(digest, workspace)
        view = self.get_view()
        if view.result.id != result:
            raise AuthorizationDenied()
        return prepare_projection(view, 'a' * 64).manifest

    async def page(self, digest, workspace, manifest, *, kind, after='', limit=50, search=None):
        await self.manifest(digest, workspace, manifest.result_id)
        values = sorted(self.get_view().claims if kind == 'claim' else self.get_view().evidence,
                        key=lambda item: item.id)
        if after and after not in {value.id for value in values}:
            raise CursorInvalid()
        values = [value for value in values if value.id > after]
        return tuple(values[:limit]), values[limit - 1].id if len(values) > limit else None

    async def evidence(self, digest, workspace, manifest, identity, claim_id):
        await self.manifest(digest, workspace, manifest.result_id)
        view = self.get_view()
        item = next((value for value in view.evidence if value.id == identity), None)
        if item is None:
            raise AuthorizationDenied()
        relation = None
        if claim_id is not None:
            claim = next((value for value in view.claims if value.id == claim_id), None)
            if claim is None or identity not in (*claim.citation_ids, *claim.counterevidence_ids):
                raise AuthorizationDenied()
            relation = 'citation' if identity in claim.citation_ids else 'counterevidence'
        return item, relation

    async def graph(self, digest, workspace, manifest, *, phase, after, limit):
        await self.manifest(digest, workspace, manifest.result_id)
        return _graph_page(self.get_view().graph, phase, after, limit)

    async def header(self, digest, workspace, manifest):
        await self.manifest(digest, workspace, manifest.result_id)
        return json.loads(prepare_projection(self.get_view(), 'a' * 64).header)
