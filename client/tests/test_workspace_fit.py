"""Top-level widgets must fit the desktop they are actually shown on.

Sizes in Qt6 are DEVICE-INDEPENDENT pixels, so "fits a 1366x768 laptop" is a
statement about 100% scaling and about nothing else: the same panel offers
roughly 911x512 of them at 150% and 683x384 at 200%. A minimum larger than the
workspace cannot be honoured - the widget opens taller than the desktop and
whatever sits along its bottom edge becomes unreachable.

Why the arithmetic is tested through a pure function
---------------------------------------------------
The first attempt at this coverage asked the REAL screen: build the window,
read `screen().availableGeometry()`, assert the minimum fits inside it. On any
ordinary monitor that assertion passes whether or not the clamp exists, because
the unclamped minimum is 960x640 and the monitor is bigger than that. It could
only have failed on a machine that already had the problem.

`clamp_to_workspace` takes the workspace as an argument, so the case that
matters - a workspace SMALLER than the preference - is an ordinary test input
rather than a property of the machine the suite happens to run on. The
real-screen assertion is kept in test_ui_smoke.py; it is not deleted, it is
just not the one doing the work.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialogButtonBox  # noqa: E402

from wuwaterm_client import theme  # noqa: E402
from wuwaterm_client.config import APPEARANCE_LIGHT, ClientConfig  # noqa: E402
from wuwaterm_client.ui.components import clamp_to_workspace  # noqa: E402
from wuwaterm_client.ui.main_window import (  # noqa: E402
    WINDOW_ABSOLUTE_MINIMUM,
    WINDOW_DEFAULT_SIZE,
    WINDOW_PREFERRED_MINIMUM,
)
from wuwaterm_client.ui.settings_dialog import (  # noqa: E402
    SETTINGS_ABSOLUTE_MINIMUM,
    SETTINGS_DEFAULT_SIZE,
    SETTINGS_PREFERRED_MINIMUM,
    SettingsDialog,
)

# 1366x768 面板在 200% 缩放下报告的逻辑工作区,取整。这是这个应用会遇到的
# 最小的现实桌面,也是设置对话框曾经放不下的那一个。
TIGHT_WORKSPACE = (683, 384)
ROOMY_WORKSPACE = (2560, 1440)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.mark.parametrize(
    "name,preferred,default,floor",
    [
        ("主窗口", WINDOW_PREFERRED_MINIMUM, WINDOW_DEFAULT_SIZE, WINDOW_ABSOLUTE_MINIMUM),
        ("设置对话框", SETTINGS_PREFERRED_MINIMUM, SETTINGS_DEFAULT_SIZE,
         SETTINGS_ABSOLUTE_MINIMUM),
    ],
)
def test_nothing_opens_larger_than_a_tight_workspace(name, preferred, default, floor):
    """683x384 的桌面上,下限与开启尺寸都不许越界。"""
    minimum, size = clamp_to_workspace(
        TIGHT_WORKSPACE, preferred, default, floor
    )
    assert minimum[0] <= TIGHT_WORKSPACE[0], f"{name} 最小宽越界:{minimum}"
    assert minimum[1] <= TIGHT_WORKSPACE[1], f"{name} 最小高越界:{minimum}"
    assert size[0] <= TIGHT_WORKSPACE[0], f"{name} 开启宽越界:{size}"
    assert size[1] <= TIGHT_WORKSPACE[1], f"{name} 开启高越界:{size}"


@pytest.mark.parametrize(
    "name,preferred,default,floor",
    [
        ("主窗口", WINDOW_PREFERRED_MINIMUM, WINDOW_DEFAULT_SIZE, WINDOW_ABSOLUTE_MINIMUM),
        ("设置对话框", SETTINGS_PREFERRED_MINIMUM, SETTINGS_DEFAULT_SIZE,
         SETTINGS_ABSOLUTE_MINIMUM),
    ],
)
def test_a_roomy_workspace_gets_the_preference_untouched(
    name, preferred, default, floor
):
    """夹取只在放不下时起作用:桌面够大时,首选值原样生效。

    没有这一条,一个把所有尺寸都钉到绝对下限的实现同样能通过上一条——
    「不越界」单独成立不了,它得和「不该缩的时候不缩」一起说。
    """
    minimum, size = clamp_to_workspace(ROOMY_WORKSPACE, preferred, default, floor)
    assert minimum == preferred, f"{name} 在大桌面上被无谓地缩小了:{minimum}"
    assert size == default, f"{name} 的开启尺寸被无谓地改动了:{size}"


def test_the_settings_dialog_can_shrink_to_a_tight_workspace(qapp, tmp_path,
                                                             monkeypatch) -> None:
    """夹取只有在内容真的能缩下去时才算数。

    对话框的三张卡片要 584 逻辑像素高,而这个桌面只有 384。把下限夹到 384 而
    内容缩不下去,窗口照样开得比屏幕高——夹取需要一个滚动区兜底,而按钮盒必须
    留在滚动区**外面**:能滚到的是内容,永远够得着的必须是决定。

    **必须挂上样式表**。卡片的内边距、控件的最小高度都来自 QSS,不挂样式表时
    内容的最小高只有一百多像素,于是「缩得下去」在**去掉滚动区之后依然成立**——
    这一条断言的第一版就是这样绿着通过变异的。这与 offscreen 平台报告 0 个字体族
    是同一类陷阱:测试环境缺了产物里有的东西,于是量到的不是产物的行为。

    断的是结果:请求缩到工作区大小之后,**它真的是那么大**。没有滚动区时
    resize 会被布局最小值顶回去,量到 584——在一块 384 高的桌面上,那就是
    按钮盒在屏幕之外。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    previous = qapp.styleSheet()
    qapp.setStyleSheet(theme.load_stylesheet(APPEARANCE_LIGHT))
    try:
        dialog = SettingsDialog(ClientConfig(base_url="https://test.invalid/api"))
        # 本机工作区很宽裕,fit_to_workspace 会把下限留在首选值上;这里要问的是
        # 内容能不能缩,所以先解除那个与本机相关的下限。
        dialog.setMinimumSize(0, 0)
        dialog.resize(*TIGHT_WORKSPACE)
        dialog.layout().activate()

        assert dialog.height() <= TIGHT_WORKSPACE[1], (
            f"对话框被布局顶回 {dialog.height()} 高,而桌面只有 {TIGHT_WORKSPACE[1]}:"
            "内容缩不下去,Ok/取消 会落在屏幕之外"
        )
        box = dialog.findChild(QDialogButtonBox)
        assert box is not None
        assert box.geometry().height() > 0, "按钮盒被压成了零高"
        assert box.geometry().bottom() <= dialog.height(), (
            f"按钮盒底边 {box.geometry().bottom()} 超出对话框 {dialog.height()}"
        )
    finally:
        qapp.setStyleSheet(previous)
