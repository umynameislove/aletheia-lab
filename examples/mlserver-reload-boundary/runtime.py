"""Minimal custom runtime; no observer hooks or research harness imports."""

import os
import uuid

import helper
from mlserver import MLModel
from mlserver.types import InferenceRequest, InferenceResponse, Parameters, ResponseOutput

RUNTIME_OFFSET = 0
RUNTIME_REVISION = "runtime-r1"


class Probe(MLModel):
    async def load(self) -> bool:
        self.instance_token = str(uuid.uuid4())
        self.runtime_revision = RUNTIME_REVISION
        self.runtime_offset = RUNTIME_OFFSET
        self.helper_revision = helper.HELPER_REVISION
        self.helper_value = helper.HELPER_VALUE
        return True

    async def predict(self, payload: InferenceRequest) -> InferenceResponse:
        x = payload.inputs[0].data.root[0]
        return InferenceResponse(
            model_name=self.name,
            id=payload.id,
            parameters=Parameters(
                server_pid=os.getpid(),
                instance_token=self.instance_token,
                runtime_revision=self.runtime_revision,
                runtime_offset=self.runtime_offset,
                helper_revision=self.helper_revision,
                helper_value=self.helper_value,
            ),
            outputs=[
                ResponseOutput(
                    name="y",
                    shape=[1],
                    datatype="INT64",
                    data=[x + self.runtime_offset + self.helper_value],
                )
            ],
        )
