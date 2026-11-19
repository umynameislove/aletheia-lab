"""Diagnostic write contention and allowlisted exception types; never a capacity certificate."""
def pytest_collection_modifyitems(items):
    assert len(items) == 1
    module = items[0].module
    original = module.calibration_profile

    def profile(**kwargs):
        base = original(**kwargs)
        return type(base).model_validate({
            **base.model_dump(),
            "run_label": "revisionprobe",
            "journeys": tuple(journey.model_copy(update={"weight": 50 if journey.name in
                ("analysis_terminal", "followup_terminal") else 1}) for journey in base.journeys),
        })

    module.calibration_profile = profile
    anchor = "owner=configure_process_observability(role=sys.argv[1]"
    assert module._CHILD.count(anchor) == 1
    diagnostic = r'''
original_complete_result = PostgresJobs.complete_result
async def diagnostic_complete_result(self, *, claim, **kwargs):
    try:
        return await original_complete_result(self, claim=claim, **kwargs)
    except Exception as error:
        known = type(error).__name__
        if known not in ('WorkspaceConflict', 'AuthorizationDenied', 'SourceNotFound', 'ValueError', 'TimeoutError'):
            known = 'other'
        print(json.dumps(dict(event='diagnostic.publication_exception', job_id=claim.job_id,
                              operation=claim.operation, exception_type=known)), file=sys.stderr, flush=True)
        raise
PostgresJobs.complete_result = diagnostic_complete_result
'''
    module._CHILD = module._CHILD.replace(anchor, diagnostic + "\n" + anchor)
    assert "diagnostic.publication_exception" in module._CHILD
