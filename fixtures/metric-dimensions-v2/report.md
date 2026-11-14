# Aletheia Lab Product Report

Generated from one immutable diagnosis-visible ProductView.

## Scope

- Schema version: `p6-product-view/v2`
- Project ID: `p3-project-2817da272a769d4738a65f33fdc4bfd50cecfe96c691845710d09a3d9e4123ed`
- Project name: Imported project
- Mode: `project_audit`
- Visibility: `diagnosis`
- Demo only: `false`
- Snapshot ID: `p3-snapshot-27a222f8b5c778c11a803ee9c102cee3e8cdee5bf4c2ac4bec37c8f0fd88063f`
- Baseline ID: `p3-snapshot-7e60f4074eb6f035ea5a8f9a2248548bcc5a312233ed91444bb19301dfde8563`
- Snapshot SHA-256: `27a222f8b5c778c11a803ee9c102cee3e8cdee5bf4c2ac4bec37c8f0fd88063f`

## Runtime

- Provider: `local_evidence`
- Model: `snapshot-metric-analysis/v1`
- External call: `false`

## Result

- Result ID: `p6-result-f3fd0a92fdeab97ca25f6e4012938ad3cf0841f8f5c5f126d5f45cb449c09594`
- Status: `complete`
- Disposition: `abstain`
- Causal status: `unverified`
- Summary: Computed 1 metric changes from stored observations.
- Independent families: `not_estimated` (`null`)
- Contexts: 1
- Outputs: 1
- Claims: 1
- Allowed wording: Computed 1 metric changes from stored observations.
- Forbidden wording: One visible project artifact caused the observed change.
- Caveat: Actual local metric analysis; causal attribution and human usability are unverified.

## Metric definitions

- `f1`; direction=`higher_is_better`; regression_threshold=`0.1`; unit=`%`

## Metric changes

### Metric change `p3-evidence-8fa3557e3b55da8401a207e7ff2725d25d017d428d4dc5887366d7fe9fb12add`

- Metric: `f1`
- Kind: `decreased`
- Before: snapshot_id=`p3-snapshot-7e60f4074eb6f035ea5a8f9a2248548bcc5a312233ed91444bb19301dfde8563`; run_id=`lần-4`; step=`1`; value=`0.8`; class=`dương tính`; unit=`%`; unit_origin=`source`
- After: snapshot_id=`p3-snapshot-27a222f8b5c778c11a803ee9c102cee3e8cdee5bf4c2ac4bec37c8f0fd88063f`; run_id=`lần-4`; step=`1`; value=`0.7`; class=`dương tính`; unit=`%`; unit_origin=`source`
- Delta: `-0.1`
- Adverse status: `adverse`

## Conversation

- Conversation ID: `p6-conversation-4d244a605095c231aa49c160ac8ff9d59a01f229b6e0226c4f409b943b72d039`

### Turn `p6-turn-13252fcbf14efd96b1bc9613a40b86047c2c942209d9bd4e4332240d5844c9aa`

- Snapshot ID: `p3-snapshot-27a222f8b5c778c11a803ee9c102cee3e8cdee5bf4c2ac4bec37c8f0fd88063f`
- Result ID: `p6-result-f3fd0a92fdeab97ca25f6e4012938ad3cf0841f8f5c5f126d5f45cb449c09594`
- Runtime provider: `local_evidence`
- Runtime model: `snapshot-metric-analysis/v1`
- External call: `false`
- Question: Which class changed under the declared threshold?
- Answer: Run lần-4, metric f1, step 1, class 'dương tính', unit '%' (source): 0.8 -\> 0.7; delta -0.1; classification adverse. These observations do not establish a cause.
- Abstained: `true`
- Visible evidence IDs: `p3-evidence-3aa7dafef4b03e1efdac189f00829c483c780c7059879c3bc172bb3ccb75a280`, `p3-evidence-3d8fc9f6a39c306e8c02b254ad2be3ec1838fedade9423ffad53b341050c0944`, `p3-evidence-47ae1dc4c11a0ee63cbc2d8b14d890de45b33de94d1c80dfa582ba8657aafb1e`, `p3-evidence-8fa3557e3b55da8401a207e7ff2725d25d017d428d4dc5887366d7fe9fb12add`, `p3-evidence-d9961fa046b88de538647f6f2d4bbd53301fad09733d8ee00c86516217a1340d`
- Missing evidence: Counterfactual comparison: not_available

## Claims

### Claim `p6-claim-3d6a9844c13e06bdd053a9e4286d5bcb8d89bdc93d682bc0270e080d549d66bb`

- Turn ID: `p6-turn-13252fcbf14efd96b1bc9613a40b86047c2c942209d9bd4e4332240d5844c9aa`
- Text: Run lần-4, metric f1, step 1, class 'dương tính', unit '%' (source): 0.8 -\> 0.7; delta -0.1; classification adverse.
- Claim type: `evidence_statement`
- Epistemic state: `observed`
- Support: `fully_supported`
- Citation IDs: `p3-evidence-8fa3557e3b55da8401a207e7ff2725d25d017d428d4dc5887366d7fe9fb12add`
- Counterevidence IDs: `none`
- Missing evidence: Counterfactual comparison: not_available
- Maximum strength: `observation`

## Visible evidence

### Evidence `p3-evidence-3aa7dafef4b03e1efdac189f00829c483c780c7059879c3bc172bb3ccb75a280`

- Kind: `snapshot`
- Title: Observed project snapshot
- Text: The analyzed project snapshot is bound to this regression evidence generation.
- Reproduction record ID: `p3-snapshot-27a222f8b5c778c11a803ee9c102cee3e8cdee5bf4c2ac4bec37c8f0fd88063f`
- Reproduction record kind: `snapshot`
- Source SHA-256: `27a222f8b5c778c11a803ee9c102cee3e8cdee5bf4c2ac4bec37c8f0fd88063f`
- Visibility: `diagnosis`
- Redacted: `false`

### Evidence `p3-evidence-3d8fc9f6a39c306e8c02b254ad2be3ec1838fedade9423ffad53b341050c0944`

- Kind: `snapshot_comparison`
- Title: Stored snapshot comparison
- Text: The stored comparison records a change between two immutable project snapshots.
- Reproduction record ID: `p3-comparison-703393baa11991941338f456a7ddfbf2393cd9448dea67a05b93efadec9844f8`
- Reproduction record kind: `snapshot_comparison`
- Source SHA-256: `703393baa11991941338f456a7ddfbf2393cd9448dea67a05b93efadec9844f8`
- Visibility: `diagnosis`
- Redacted: `false`

### Evidence `p3-evidence-47ae1dc4c11a0ee63cbc2d8b14d890de45b33de94d1c80dfa582ba8657aafb1e`

- Kind: `regression_candidate`
- Title: Regression candidate
- Text: The stored event is a non-causal regression candidate with unverified causal status.
- Reproduction record ID: `p3-event-4b0a5d0eda5808c14970651a17c56a1e335e5b4f899862252a6f43ce6f36b67d`
- Reproduction record kind: `regression_event`
- Source SHA-256: `4b0a5d0eda5808c14970651a17c56a1e335e5b4f899862252a6f43ce6f36b67d`
- Visibility: `diagnosis`
- Redacted: `false`

### Evidence `p3-evidence-8fa3557e3b55da8401a207e7ff2725d25d017d428d4dc5887366d7fe9fb12add`

- Kind: `metric_observation`
- Title: Observed metric change
- Text: Run lần-4, metric f1, step 1, class 'dương tính', unit '%' (source): 0.8 -\> 0.7; delta -0.1; classification adverse.
- Reproduction record ID: `p3-comparison-703393baa11991941338f456a7ddfbf2393cd9448dea67a05b93efadec9844f8`
- Reproduction record kind: `snapshot_comparison`
- Source SHA-256: `afdff9aa17f4120bcb56f6a146bd87119706ddd525cdf44b3beb868f7d4e53c8`
- Visibility: `diagnosis`
- Redacted: `false`

### Evidence `p3-evidence-d9961fa046b88de538647f6f2d4bbd53301fad09733d8ee00c86516217a1340d`

- Kind: `snapshot`
- Title: Baseline project snapshot
- Text: A baseline snapshot is bound to this regression evidence generation.
- Reproduction record ID: `p3-snapshot-7e60f4074eb6f035ea5a8f9a2248548bcc5a312233ed91444bb19301dfde8563`
- Reproduction record kind: `snapshot`
- Source SHA-256: `7e60f4074eb6f035ea5a8f9a2248548bcc5a312233ed91444bb19301dfde8563`
- Visibility: `diagnosis`
- Redacted: `false`

## Graph and lineage references

### Nodes

- ID=`p6-node-49836354f1e49bc83ecbbd366df1280e2e9cec67a32fb1e774428751c845862e`; kind=`EvidenceItem`; source_id=`p3-evidence-3d8fc9f6a39c306e8c02b254ad2be3ec1838fedade9423ffad53b341050c0944`; visibility=`diagnosis`
- ID=`p6-node-5e45c2f847cf357be0cb646506522987fcf42172fde3650b73b453ef3d5916d8`; kind=`EvidenceItem`; source_id=`p3-evidence-47ae1dc4c11a0ee63cbc2d8b14d890de45b33de94d1c80dfa582ba8657aafb1e`; visibility=`diagnosis`
- ID=`p6-node-7f1ade524fdc63f4037372d4c8c1d0cb945b19e9afdc5901ce2320a1a24a63ff`; kind=`Disposition`; source_id=`p6-result-f3fd0a92fdeab97ca25f6e4012938ad3cf0841f8f5c5f126d5f45cb449c09594`; visibility=`diagnosis`
- ID=`p6-node-815dda2b703ee68bb1e095831f936e1b7afc1ceafb150eccabd530edf18976d8`; kind=`AtomicClaim`; source_id=`p6-claim-3d6a9844c13e06bdd053a9e4286d5bcb8d89bdc93d682bc0270e080d549d66bb`; visibility=`diagnosis`
- ID=`p6-node-aeca0398a514d0ae3a9d553dec870b0f594fe73641edf6f40ce6c624d48e2a43`; kind=`EvidenceItem`; source_id=`p3-evidence-3aa7dafef4b03e1efdac189f00829c483c780c7059879c3bc172bb3ccb75a280`; visibility=`diagnosis`
- ID=`p6-node-cb5bfed70d9df6af877c44b54a8f55a58b91ffd18824afa909eb53001c61bd73`; kind=`EvidenceItem`; source_id=`p3-evidence-8fa3557e3b55da8401a207e7ff2725d25d017d428d4dc5887366d7fe9fb12add`; visibility=`diagnosis`
- ID=`p6-node-fc6d94b7ec745e39f6982f991b6022495a953bd0d6690775276d674a14425dea`; kind=`Snapshot`; source_id=`p3-snapshot-27a222f8b5c778c11a803ee9c102cee3e8cdee5bf4c2ac4bec37c8f0fd88063f`; visibility=`diagnosis`
- ID=`p6-node-fca4dd9382f0336566ab1516e1283baea3e471abe89d144b9f9b373b4a651cca`; kind=`EvidenceItem`; source_id=`p3-evidence-d9961fa046b88de538647f6f2d4bbd53301fad09733d8ee00c86516217a1340d`; visibility=`diagnosis`

### Edges

- ID=`p6-edge-119336cccbb8a8daca8e5f494cac87c653710eae6035ade8d8cd7fd8d17f4df2`; kind=`OBSERVED_IN`; source=`p6-node-49836354f1e49bc83ecbbd366df1280e2e9cec67a32fb1e774428751c845862e`; target=`p6-node-fc6d94b7ec745e39f6982f991b6022495a953bd0d6690775276d674a14425dea`; visibility=`diagnosis`
- ID=`p6-edge-51a423f46101a891f4fe86a11156c3dda5e30cbe4b6f79e7bf36056063b6a869`; kind=`ASSIGNED_DISPOSITION`; source=`p6-node-815dda2b703ee68bb1e095831f936e1b7afc1ceafb150eccabd530edf18976d8`; target=`p6-node-7f1ade524fdc63f4037372d4c8c1d0cb945b19e9afdc5901ce2320a1a24a63ff`; visibility=`diagnosis`
- ID=`p6-edge-b4fc162974c13a633f79cba33a3c107525639d4803010d486aa51c746544cbe0`; kind=`OBSERVED_IN`; source=`p6-node-cb5bfed70d9df6af877c44b54a8f55a58b91ffd18824afa909eb53001c61bd73`; target=`p6-node-fc6d94b7ec745e39f6982f991b6022495a953bd0d6690775276d674a14425dea`; visibility=`diagnosis`
- ID=`p6-edge-c514f832baf9c820a0835a549eb309295412fe0898aa00e4602e4ba264650785`; kind=`OBSERVED_IN`; source=`p6-node-aeca0398a514d0ae3a9d553dec870b0f594fe73641edf6f40ce6c624d48e2a43`; target=`p6-node-fc6d94b7ec745e39f6982f991b6022495a953bd0d6690775276d674a14425dea`; visibility=`diagnosis`
- ID=`p6-edge-f9bcb4b356bb37ca2f1a097d0eec2fdd81851c215d586a408fab57b92d60e92b`; kind=`CITES`; source=`p6-node-815dda2b703ee68bb1e095831f936e1b7afc1ceafb150eccabd530edf18976d8`; target=`p6-node-cb5bfed70d9df6af877c44b54a8f55a58b91ffd18824afa909eb53001c61bd73`; visibility=`diagnosis`

## Limitations

- Actual local metric analysis; causal attribution and human usability are unverified.
- Counterfactual comparison: not_available
