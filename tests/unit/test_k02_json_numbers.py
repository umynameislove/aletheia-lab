"""A nonzero JSON number must never silently become zero at the hosted boundary."""

import math

import pytest
from pydantic import BaseModel, ConfigDict

from aletheia_lab.hosted.request_boundary import RequestRejected, decode_json_model


class Number(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: float | str


@pytest.mark.parametrize("token", ["1e-400", "-1e-400", "1.000e-9999", "-0.0001E-9999"])
def test_nonzero_underflow_is_rejected_without_echoing_the_number(token):
    with pytest.raises(RequestRejected) as caught:
        decode_json_model(('{"value":' + token + '}').encode(), Number, maximum_bytes=1024)
    assert caught.value.status == 400 and caught.value.code == "invalid_json"
    assert token not in str(caught.value)


@pytest.mark.parametrize("token", ["0e400", "0.000e-9999", "-0.00E+9999", "1e-300", "5e-324", "-5e-324"])
def test_true_zero_and_representable_tiny_values_remain_distinct(token):
    result = decode_json_model(('{"value":' + token + '}').encode(), Number, maximum_bytes=1024)
    assert isinstance(result.value, float) and math.isfinite(result.value)
    assert result.value == float(token)
    if "5e-324" in token or token == "1e-300":
        assert result.value != 0.0


def test_quoted_numeric_text_is_not_reinterpreted():
    assert decode_json_model(b'{"value":"1e-400"}', Number, maximum_bytes=1024).value == "1e-400"
