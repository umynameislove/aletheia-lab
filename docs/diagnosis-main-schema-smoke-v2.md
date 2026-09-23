# Diagnosis main schema smoke: forward formatting correction

The registered run remains unchanged: 128 deterministic completions and 896
provider-backed technical failures. The first synthetic schema smoke consumed
one provider call and returned a response, but local validation against the
original response schema failed with `GatewayContractError`. Its immutable
receipt is `4a0c2f722cad5116c18aebbf52bd625f5831a84b838df011973cdc46f3d51825`.
No raw response was retained, so the exact offending field cannot be recovered
from that attempt. The 896-request recovery remains unauthorized.

The first smoke's provider-facing schema removed six string `pattern` constraints;
all other schema constraints stayed in place. The response reaching local validation
shows that this projected transport passed the earlier request-rejection point.
It does **not** prove which pattern failed, that the response satisfied every
provider-facing constraint, or that the main-study outputs would be valid.
OpenAI documents `pattern` as part of its supported Structured Outputs subset,
but support for the keyword does not establish compatibility with every regex
in the original contract. [Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs)

The successor smoke is prospective and synthetic-only. It keeps the same model
snapshot, prompt, sampling and token ceiling. The outbound payload differs
only in the per-attempt request ID and one response-schema constraint: the
registered `claim-[1-5]` regex becomes an equivalent five-value `enum`, which
does not rely on provider regex support. The other five regexes remain absent
from the wire and mandatory in local validation. The successor binds the
consumed smoke receipt and permits one narrowly defined local formatting
repair: remove leading/trailing whitespace from claim text, material-part IDs
and text, cited evidence IDs, and an abstention reason when present, only in
fields that failed the original regex. It never
collapses internal whitespace, edits a claim's words, changes a label or
status, adds/removes citations, or alters array structure. A repaired output
must still pass the full original schema and A2 citation/semantic validator.
The synthetic pass additionally requires at least one claim citing the supplied
synthetic evidence, so an empty envelope cannot give a false-positive pass.
Outputs failing either check remain invalid. The receipt stores only the raw
response hash, accepted-payload hash, safe schema field paths and error code;
it does not store generated text. Unlike the first receipt, a failure can now
identify the violated field without revealing its value.

This is a forward technical correction, not a rewrite of the original run or
its scientific design. A passing successor smoke would establish only this
synthetic transport-and-validation gate. Any later 896-request recovery needs
its own authorization and must apply the same declared repair policy, retain
technical-failure accounting, report the number of repaired outputs, and show
a sensitivity result treating those outputs as unaccepted. Neither a passing
smoke nor an amended recovery may be described as the original registered
attempt having passed.
