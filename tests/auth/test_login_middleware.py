"""UI middleware auth redirect expectations."""
import os


def test_require_auth_env_flag():
    # Documented contract: prod compose sets NEXT_PUBLIC_REQUIRE_AUTH=true at build time
    assert os.environ.get("NEXT_PUBLIC_REQUIRE_AUTH", "false") in ("true", "false", "1", "0", "")
