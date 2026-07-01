import os
from unittest.mock import patch
from app.config import get_env_bool

def test_get_env_bool():
    with patch.dict(os.environ, {"TEST_TRUE": "true", "TEST_FALSE": "false", "TEST_1": "1", "TEST_0": "0", "TEST_YES": "yes", "TEST_NO": "no", "TEST_EMPTY": ""}):
        assert get_env_bool("TEST_TRUE") is True
        assert get_env_bool("TEST_FALSE") is False
        assert get_env_bool("TEST_1") is True
        assert get_env_bool("TEST_0") is False
        assert get_env_bool("TEST_YES") is True
        assert get_env_bool("TEST_NO") is False
        assert get_env_bool("TEST_EMPTY") is False
        assert get_env_bool("NON_EXISTENT") is False
        assert get_env_bool("NON_EXISTENT", True) is True
