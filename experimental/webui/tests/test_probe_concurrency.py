"""Web request validation uses the shared probe worker bounds."""

import pytest
from pydantic import ValidationError

from experimental.webui.experimental_models import RedditRunRequest, SearxngRunRequest


@pytest.mark.parametrize("model, fields", [
    (RedditRunRequest, {}),
    (SearxngRunRequest, {"instance_url": "http://example.test", "query": "index"}),
])
@pytest.mark.parametrize("workers", [None, 1, 3, 12, 16, 17, 0])
def test_probe_worker_bounds(model, fields, workers):
    if workers in (17, 0):
        with pytest.raises(ValidationError, match="between 1 and 16"):
            model(**fields, probe_worker_count=workers)
    else:
        assert model(**fields, probe_worker_count=workers).probe_worker_count == workers
