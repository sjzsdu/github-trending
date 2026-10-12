"""CLI 路由与参数校验测试（不触网、不启动浏览器）。"""

import pytest

from ghtrend.cli import PLATFORMS, build_parser, validate_args


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def test_parse_platform_action():
    a = _parse("juejin", "login", "--account", "main")
    assert a.platform == "juejin"
    assert a.action == "login"
    assert a.account == "main"


def test_parse_publish_with_article():
    a = _parse("juejin", "publish", "--article", "artifacts/x.md", "--mode", "publish")
    assert a.article == "artifacts/x.md"
    assert a.mode == "publish"


def test_parse_run():
    a = _parse("run", "--limit", "2", "--platforms", "juejin,toutiao", "--mode", "draft")
    assert a.platform == "run"
    assert a.action is None
    assert a.limit == 2
    assert a.platforms == "juejin,toutiao"


def test_parse_defaults():
    a = _parse("juejin", "check")
    assert a.account == "default"


def test_platform_requires_action():
    err = validate_args(_parse("juejin"))
    assert err and "login|check|publish" in err


def test_unknown_platform_rejected():
    err = validate_args(_parse("zhihu", "login"))
    assert err and "未知平台" in err


def test_run_rejects_action():
    err = validate_args(_parse("run", "login"))
    assert err and "run" in err


def test_publish_requires_article_or_latest():
    err = validate_args(_parse("juejin", "publish"))
    assert err and "--article" in err
    assert validate_args(_parse("juejin", "publish", "--latest")) is None


def test_action_choices_enforced():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["juejin", "fly"])


def test_platforms_registry():
    assert "juejin" in PLATFORMS
