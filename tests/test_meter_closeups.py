"""Close-ups preserve source regions and unknown answers, not invented evidence."""

import base64
import io
import json

import pytest
from PIL import Image, ImageDraw

from labagent.meter_live_model import detail_crops, infer_closeups, infer_part


class ReplyOpener:
    def __init__(self, replies, finish="stop"):
        self.replies = iter(replies)
        self.finish = finish
        self.payloads = []

    def open(self, request, timeout):
        assert timeout <= 12
        self.payloads.append(json.loads(request.data))
        return io.BytesIO(
            json.dumps(
                {"choices": [{"finish_reason": self.finish, "message": {"content": next(self.replies)}}]}
            ).encode()
        )


def instrument():
    image = Image.new("RGB", (400, 800), "gray")
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((0, 208, 400, 630), fill="red")
    drawing.rectangle((0, 632, 400, 800), fill="blue")
    return image


def test_two_closeups_use_one_source_and_only_their_own_protocol():
    opener = ReplyOpener(["Yv", "YN"])
    result = infer_closeups(instrument(), "http://127.0.0.1:8034", opener)
    assert result["checks"] == {"black_in_com": True, "red_in_voltage": False, "dc_voltage_mode": True}
    assert result["meter_range"] == "dc_20v"
    assert [view["part"] for view in result["detail_views"]] == ["dial", "terminals"]
    assert len(opener.payloads) == 2
    centers = []
    for view, payload in zip(result["detail_views"], opener.payloads):
        assert payload["temperature"] == 0 and payload["max_tokens"] == 8
        assert payload["cache_prompt"] is False
        sent = payload["messages"][0]["content"][1]["image_url"]["url"]
        assert view["image_url"] == sent
        with Image.open(io.BytesIO(base64.b64decode(sent.split(",", 1)[1]))) as image:
            assert image.size == (320, 320)
            centers.append(image.getpixel((160, 160)))
    assert centers[0][0] > 200 and centers[0][2] < 30
    assert centers[1][2] > 200 and centers[1][0] < 30


def test_unknown_closeups_never_become_positive_or_a_guessed_range():
    result = infer_closeups(instrument(), "http://127.0.0.1:8034", ReplyOpener(["??", "??"]))
    assert all(value is None for value in result["checks"].values())
    assert result["meter_range"] is None


@pytest.mark.parametrize(
    "part,reply,finish",
    [
        ("dial", "YY", "stop"),
        ("terminals", "Yv", "stop"),
        ("dial", "Yv explanation", "stop"),
        ("dial", "Yv", "length"),
        ("terminals", "", "stop"),
    ],
)
def test_invalid_or_truncated_part_cannot_satisfy_other_part(part, reply, finish):
    with pytest.raises(ValueError):
        infer_part(instrument(), part, "http://127.0.0.1:8034", ReplyOpener([reply], finish))


@pytest.mark.parametrize("size", [(640, 480), (150, 240), (200, 230)])
def test_sideways_or_too_small_source_is_rejected_without_fake_detail(size):
    image = Image.new("RGB", size)
    assert detail_crops(image) is None
    with pytest.raises(ValueError):
        infer_closeups(image, "http://127.0.0.1:8034", ReplyOpener([]))
