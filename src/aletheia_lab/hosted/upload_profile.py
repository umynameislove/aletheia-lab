"""The API and worker must use the same complete upload/preview profile."""
from .postgres_sources import PostgresSources
from .preview_leases import PreviewLeases


async def upload_profile(sources: PostgresSources, leases: PreviewLeases) -> bool:
    staging_ready = await sources.is_upload_ready()
    preview_ready = await leases.is_ready()
    if staging_ready != preview_ready:
        raise RuntimeError("Upload staging and preview migrations must be ready together.")
    return staging_ready
