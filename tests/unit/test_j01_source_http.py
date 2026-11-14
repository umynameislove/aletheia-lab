"""Source HTTP security wiring with real CSRF library and synthetic storage."""
import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta

from pydantic import SecretStr

from aletheia_lab.hosted.postgres_sources import SourceReceipt
from aletheia_lab.hosted.read_backend import SessionRecord
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.source_http import SourceUploadApplication
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_contract.session_capabilities import Actor


def scenario(tmp_path, *, valid_csrf=True, payload=b'zip-placeholder', maximum=1000, key=True):
    async def run():
        tokens = CsrfTokens(SecretStr('synthetic-key-' + 's' * 43))
        token = tokens.issue('ctx1')
        record = SessionRecord(Actor(user_id='u1',display_name='User',email='u@example.invalid',email_verified=True),
            'ctx1',datetime.now(UTC)+timedelta(minutes=5),token,'ready','ws1','active',True)
        effects, reads, messages = [], [], []
        class Sessions:
            async def is_ready(self): return True
            async def resolve_cookie(self,cookie): return record
        class Sources:
            async def put(self,**kwargs):
                effects.append(kwargs)
                return SourceReceipt('src1','ws1',hashlib.sha256(kwargs['payload']).hexdigest())
        runtime=RuntimeSettings.model_validate({'environment':'staging','public_origin':'https://app.example.invalid',
            'identity':{'issuer':'https://id.example.invalid','client_id':'test','redirect_uri':'https://app.example.invalid/v1/auth/callback','client_kind':'public'},
            'storage':{'database_url_env':'ALETHEIA_HOSTED_DATABASE_URL','object_root':str(tmp_path)}})
        async def clock(): return True
        async def fallback(scope,receive,send): raise AssertionError('unexpected fallback')
        app=SourceUploadApplication(fallback,Sources(),Sessions(),tokens,runtime,
            body_limits=BodyLimits(maximum,1,3,10),clock_check=clock)
        headers=[(b'host',b'app.example.invalid'),(b'origin',b'https://app.example.invalid'),
            (b'content-type',b'application/zip'),(b'content-length',str(len(payload)).encode()),
            (b'cookie',f'__Host-aletheia_session={"a"*43}; __Host-aletheia_csrf={token.get_secret_value()}'.encode()),
            (b'x-csrf-token',token.get_secret_value().encode() if valid_csrf else b'wrong')]
        if key:
            headers.append((b'idempotency-key',b'synthetic-request-001'))
        async def receive():
            reads.append(True)
            return {'type':'http.request','body':payload,'more_body':False}
        async def send(message): messages.append(message)
        await app({'type':'http','method':'POST','scheme':'https','path':'/v1/workspaces/ws1/sources','headers':headers},receive,send)
        return messages,effects,reads
    return asyncio.run(run())


def test_upload_only_reaches_storage_with_csrf_and_bounded_body(tmp_path):
    messages,effects,reads=scenario(tmp_path)
    assert messages[0]['status']==201 and len(effects)==len(reads)==1
    assert effects[0]['workspace_id']=='ws1'
    assert json.loads(messages[1]['body'])['sha256']==hashlib.sha256(effects[0]['payload']).hexdigest()
    assert (b'cache-control',b'no-store') in messages[0]['headers']


def test_csrf_and_oversize_and_missing_key_prevent_storage(tmp_path):
    for kwargs,status in [({'valid_csrf':False},403),({'maximum':2},413),({'key':False},400)]:
        messages,effects,reads=scenario(tmp_path,**kwargs)
        assert messages[0]['status']==status and not effects and not reads
