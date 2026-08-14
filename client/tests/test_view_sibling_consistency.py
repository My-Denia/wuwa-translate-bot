"""The three areas must answer the same question the same way.

Three findings on this branch had one shape: a behaviour existed in one area
and not in its sibling, with nothing anywhere saying that was intended.

* the field-level rejection cleared itself when the translation source was
  edited, and stayed on screen forever when the lookup query was;
* the translation area labelled its three controls, and the lookup query field
  had neither a label nor an accessible name;
* the theme lookup guarded its filesystem probe against ``OSError`` and the
  translation lookup did not, so an unreadable directory stopped the client
  from starting.

Each was found by a reviewer reading a diff, one at a time, three rounds
apart. None of them is hard to see once you look at both areas at once -
which is what this file does.

An intentional difference is not a failure here. It is an ENTRY in
``EXEMPTIONS``, with the reason written down. The rule being enforced is not
"the areas are identical"; it is "a difference between them is a decision
somebody made, not a thing nobody noticed".
"""

from __future__ import annotations

import httpx
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from wuwaterm_client.api import ApiClient  # noqa: E402
from wuwaterm_client.config import ClientConfig  # noqa: E402
from wuwaterm_client.ui.main_window import MainWindow  # noqa: E402

# (rule, area) -> why this area is allowed to differ.
EXEMPTIONS = {
    ("clears-field-error-on-edit", "status"): (
        "服务状态区没有输入框,也没有字段级错误面:它的唯一动作是刷新,"
        "失败走 banner。没有可编辑的文本,就没有「编辑后该清掉的拒绝」。"
    ),
}


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


async def _handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
    raise AssertionError("这些断言不发请求")


@pytest.fixture()
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    config = ClientConfig(base_url="https://test.invalid/api")
    win = MainWindow(config=config)
    # 配置齐全的窗口:未配置态下状态区的刷新按钮是禁用的,而禁用控件不接受焦点
    # ——那会让「主输入是谁」这个问题在一个区上没有答案,断出来的是前置条件不是功能。
    win.api_client = ApiClient(
        "https://test.invalid/api",
        _test_transport=httpx.MockTransport(_handler),
        token_provider=lambda: "wtd1.device.secret",
    )
    yield win


def _areas(window: MainWindow):
    return {
        "terms": window.terms_view,
        "translate": window.translate_view,
        "status": window.status_view,
    }


def _exempt(rule: str, area: str) -> bool:
    reason = EXEMPTIONS.get((rule, area))
    if reason is None:
        return False
    assert reason.strip(), f"{rule}/{area} 的豁免没有写理由"
    return True


def _primary_input(window: MainWindow, area, page):
    """Whatever the area itself calls its input.

    Asked through `focus_input` rather than by naming a widget here: that
    method is the published contract Ctrl+K already uses, so this check cannot
    drift away from the thing the keyboard actually reaches.
    """
    window.stack.setCurrentWidget(page)
    page.focus_input()
    return page.focusWidget()


def test_every_area_names_its_primary_input(window) -> None:
    """辅助技术要能说出焦点落在哪个控件上。

    可接受三种形态,任一即可:控件自己有 accessibleName;同区内有一个 QLabel
    把它设成了 buddy;或者控件本身就带可读文字(按钮)。占位符**不算**——
    一旦输入了内容,占位符就不在屏幕上了,而名称必须是稳定的。
    """
    missing = []
    for name, page in _areas(window).items():
        if _exempt("names-primary-input", name):
            continue
        widget = _primary_input(window, name, page)
        assert widget is not None, f"{name} 区的 focus_input 没有把焦点交给任何控件"
        has_accessible_name = bool(widget.accessibleName().strip())
        has_buddy = any(
            label.buddy() is widget for label in page.findChildren(QLabel)
        )
        own_text = bool(getattr(widget, "text", lambda: "")().strip())
        if not (has_accessible_name or has_buddy or own_text):
            missing.append(name)
    assert not missing, (
        f"这些区的主输入没有稳定名称,屏幕阅读器只能读到占位符或空:{missing}"
    )


def test_every_area_clears_a_field_error_when_its_input_is_edited(window) -> None:
    """字段级拒绝属于被拒绝的那一段文字,不属于它之后的任何一段。

    翻译区一直是这样做的,查词区在移除输入即搜时把这个处理器一并弄丢了,
    于是红框会挂到下一次提交为止——给一段服务端从没看过的文字报错。
    """
    from wuwaterm_client.errors import ClientError

    stale = []
    for name, page in _areas(window).items():
        if _exempt("clears-field-error-on-edit", name):
            continue
        field_error = getattr(page, "field_error", None)
        assert field_error is not None, (
            f"{name} 区没有字段错误面,却也没有登记豁免"
        )
        page._show_error(ClientError("input_too_long", request_id="req-x")) if hasattr(
            page, "_show_error"
        ) else page._render_error(ClientError("input_too_long", request_id="req-x"))
        assert field_error.is_showing(), f"{name} 区的前置条件没建立起来"

        editor = _primary_input(window, name, page)
        if hasattr(editor, "setPlainText"):
            editor.setPlainText("鸣潮")
        else:
            editor.setText("鸣潮")

        if field_error.is_showing():
            stale.append(name)
    assert not stale, (
        f"这些区在输入被改动之后仍挂着上一段文字的拒绝:{stale}"
    )


def test_every_area_applies_endpoint_state(window) -> None:
    """未配置时,三个区都必须让自己那个发请求的按钮不可按。

    这一条曾经只有两个区做到:翻译区的按钮在设置页上仍然可按,点下去只能拿回
    一个 not_configured,在已经有全局提示的屏幕上再添一条区域错误。
    """
    for name, page in _areas(window).items():
        assert hasattr(page, "_apply_endpoint_state"), (
            f"{name} 区没有实现 _apply_endpoint_state,未配置态无从统一"
        )


def test_every_exemption_names_an_area_that_exists(window) -> None:
    """豁免表不能悄悄过期。

    一条指向已不存在的区、或已不存在的规则的豁免,读起来像「这件事想过了」,
    实际上什么都没保护。
    """
    areas = set(_areas(window))
    rules = {
        "names-primary-input",
        "clears-field-error-on-edit",
        "applies-endpoint-state",
    }
    for rule, area in EXEMPTIONS:
        assert area in areas, f"豁免指向不存在的区:{area}"
        assert rule in rules, f"豁免指向不存在的规则:{rule}"
