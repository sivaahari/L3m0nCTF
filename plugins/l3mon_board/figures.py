"""The small number rule the Guide and the scoreboard share."""
import math


def js_round(value) -> int:
    """Round half up, as the demo's JavaScript (Math.round) does: 12.5 is 13, 2.5 is 3. Python's own round() takes a half to the even number
    (12.5 is 12), which would draw a bar one step shorter than the approved demo."""
    return int(math.floor(float(value) + 0.5))
