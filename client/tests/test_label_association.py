"""Every labelled control must be ATTACHED to its label, not merely beside it.

A `QLabel` placed next to a field is a visual arrangement. Assistive
technology reads the two as unrelated objects, so the field announces itself
as an unnamed edit box - and a placeholder does not close the gap, because it
is gone the moment anything is typed.

Why this is a whole file, and why it reaches into the dialogs
-------------------------------------------------------------
Before the redesign the settings screen was a `QFormLayout`, and
`addRow("文字", widget)` sets the buddy for you. Replacing it with cards and
hand-built rows dropped that association silently: no constant disappeared and
no widget class disappeared. The API census missed it too, for a reason worth
recording - it compared the WHOLE TREE, and the status area still calls
`addRow`, so the name never left the set. A capability can be lost in the one
place it mattered while the name lives on somewhere else entirely. The census
now also compares per module.

The sibling-consistency gate covers the three AREAS. Dialogs are not areas, and
that is exactly where the association went missing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
)

from wuwaterm_client.config import ClientConfig  # noqa: E402
from wuwaterm_client.ui.first_run_dialog import FirstRunDialog  # noqa: E402
from wuwaterm_client.ui.settings_dialog import SettingsDialog  # noqa: E402
from wuwaterm_client.ui.token_dialog import TokenDialog  # noqa: E402

# 需要名字的控件类型:能接受输入或能被键盘操作的那些。纯展示的 QLabel 不在内。
NAMED_CONTROL_TYPES = (QLineEdit, QPlainTextEdit, QDoubleSpinBox, QComboBox)

# (对话框, 控件属性名) -> 为什么这个控件目前允许没有关联标签。
# 豁免不是「算了」,是一条写下了理由、并且有人能看见的决定。
EXEMPTIONS: dict[tuple[str, str], str] = {
}


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _dialogs(tmp_path):
    config = ClientConfig(base_url="https://test.invalid/api")
    return {
        "settings": SettingsDialog(config),
        "first-run": FirstRunDialog(),
        "token": TokenDialog(),
    }


def _named(widget, container) -> bool:
    if widget.accessibleName().strip():
        return True
    return any(label.buddy() is widget for label in container.findChildren(QLabel))


@pytest.mark.parametrize("dialog_name", ["settings", "first-run", "token"])
def test_every_input_in_a_dialog_has_an_attached_name(
    qapp, tmp_path, monkeypatch, dialog_name
) -> None:
    """对话框里每一个可输入控件,都要有 accessibleName 或一个把它设为 buddy 的标签。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    dialog = _dialogs(tmp_path)[dialog_name]

    unnamed = []
    controls = [c for kind in NAMED_CONTROL_TYPES for c in dialog.findChildren(kind)]
    for control in controls:
        # 隐藏在其它控件内部的子部件(比如 spinbox 自带的 QLineEdit)不单独要求
        # 名字:它由外层控件代表。
        if isinstance(control.parent(), NAMED_CONTROL_TYPES):
            continue
        attribute = next(
            (name for name in dir(dialog)
             if not name.startswith("__") and getattr(dialog, name, None) is control),
            control.objectName() or control.__class__.__name__,
        )
        if EXEMPTIONS.get((dialog_name, attribute)):
            continue
        if not _named(control, dialog):
            unnamed.append(attribute)

    assert not unnamed, (
        f"{dialog_name} 对话框里这些控件没有关联标签,读屏只能报「无名输入框」:"
        f"{unnamed}"
    )


def test_the_settings_dialog_names_the_controls_the_old_form_layout_named(
    qapp, tmp_path, monkeypatch
) -> None:
    """逐条对上 main 的 QFormLayout.addRow 曾经免费给出的那三条关联。

    上一条是通用规则;这一条钉死具体的三个控件,因为它们是**实际丢过**的那三个。
    通用规则将来可能因为控件类型清单的调整而漏掉某一个;这一条不会。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    dialog = SettingsDialog(ClientConfig(base_url="https://test.invalid/api"))

    for attribute in ("base_url_edit", "timeout_spin", "backend_label"):
        control = getattr(dialog, attribute)
        assert _named(control, dialog), (
            f"{attribute} 失去了 main 的 QFormLayout 自动给出的标签关联"
        )


def test_no_exemption_outlives_the_thing_it_exempts(qapp, tmp_path, monkeypatch) -> None:
    """豁免过期了要红。

    一条指向已经修好的控件的豁免,读起来像「这件事想过了」,实际上什么都不保护,
    而且会掩盖它下一次真的坏掉。这一条要求:每条豁免指向的控件必须存在,且必须
    **确实还没有**关联标签——修好之后这条断言会红,提醒把豁免删掉。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    dialogs = _dialogs(tmp_path)
    for (dialog_name, attribute), reason in EXEMPTIONS.items():
        assert reason.strip(), f"{dialog_name}/{attribute} 的豁免没有写理由"
        dialog = dialogs.get(dialog_name)
        assert dialog is not None, f"豁免指向不存在的对话框:{dialog_name}"
        control = getattr(dialog, attribute, None)
        assert control is not None, f"豁免指向不存在的控件:{dialog_name}.{attribute}"
        assert not _named(control, dialog), (
            f"{dialog_name}.{attribute} 已经有关联标签了,这条豁免该删掉"
        )


def test_the_token_field_is_masked_in_every_dialog_that_takes_one(
    qapp, tmp_path, monkeypatch
) -> None:
    """令牌输入框默认必须是遮蔽的,而且「显示」得是一次明确的动作。

    这一条是掉落物普查按模块比对时冒出来的:main 的 first_run_dialog 自己调
    setEchoMode(Password),改版后那一行不在这个模块里了——它搬进了共用的密码
    字段组件。搬家不是丢失,但**当时没有任何一条断言在看这件事**:令牌是不是
    还遮着,取决于一个没人验证过的组件。

    一个默认明文的令牌框在截图、录屏、共享桌面里就是凭据泄露,而它在界面上
    看起来完全正常。
    """
    from PySide6.QtWidgets import QLineEdit

    monkeypatch.setenv("APPDATA", str(tmp_path))
    for name in ("first-run", "token"):
        dialog = _dialogs(tmp_path)[name]
        edit = dialog.token_edit
        assert edit.echoMode() == QLineEdit.EchoMode.Password, (
            f"{name} 对话框的令牌框默认是明文的"
        )
