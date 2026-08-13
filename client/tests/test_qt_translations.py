"""Qt 自带部件上的字也必须是中文。

这一整个文件补的是一个门看不见的洞。
client/tests/test_ui_strings_source.py 证明的是「这个程序显示的每一个字面量
都来自 strings.py」——那是关于**我们写的字**的陈述。用户读到的字不止这些:
在输入框里点右键弹出的撤销 / 剪切 / 复制 / 粘贴 / 全选,以及标准按钮盒的默认
按钮名,都由 Qt 自己提供,一次也不经过我们的 setText。静态门可以全绿,而那些
字仍然是英文——实际上在改版做完、套件 253 项全绿之后,它们就是英文的,是
右键点开菜单才发现的。

所以这里断的是**结果**:菜单上出现的是不是中文。不断「翻译器装上了没有」,
因为装上了但文件是错的域、Qt 换了文件名、打包漏了目标目录,每一种都是装上了
而菜单仍是英文。断结果的测试对这些一视同仁。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit  # noqa: E402

from wuwaterm_client import i18n  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app

CLIENT_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = CLIENT_ROOT / "WuwaTerm.spec"

# 一个 CJK 统一表意文字。菜单项里只要有一个,这一项就不是英文。
CJK = re.compile(r"[一-鿿]")

# Qt 未翻译时这些部件菜单上的原文。逐条列出而不是只看有没有中文,是因为
# 「菜单是空的」也会让「没有英文」成立。
ENGLISH_MENU_ENTRIES = ("Undo", "Redo", "Cut", "Copy", "Paste", "Select All")


def _menu_texts(widget) -> list[str]:
    """右键菜单上的文案,去掉助记符与快捷键那一段。"""
    menu = widget.createStandardContextMenu()
    texts = []
    for action in menu.actions():
        text = action.text()
        if not text:
            continue  # 分隔线
        texts.append(text.replace("&", "").split("\t")[0].strip())
    return texts


def test_the_translation_file_is_found_where_the_application_looks() -> None:
    """先证明找得到,后面的失败才好归因。"""
    found = i18n.find_translation_file()
    assert found is not None, (
        "找不到 qtbase 的中文翻译文件;搜索路径:"
        + ", ".join(str(p) for p in i18n.translation_search_paths())
    )
    assert found.name == i18n.TRANSLATION_FILE_NAME
    assert found.is_file()


@pytest.mark.parametrize("factory", (QLineEdit, QPlainTextEdit))
def test_the_input_context_menu_is_chinese(qapp, factory) -> None:
    """右键菜单是这个洞最早露出来的地方,也是它最容易复发的地方。

    两种输入部件都测:查词框是 QLineEdit(选它是因为中文输入法的候选阶段不改
    变 text),原文框是 QPlainTextEdit,两者的菜单来自不同的类。
    """
    assert i18n.install_qt_translations(qapp) is True

    texts = _menu_texts(factory())

    assert texts, "菜单是空的,这样『没有英文』会毫无意义地成立"
    for entry in ENGLISH_MENU_ENTRIES:
        assert entry not in texts, f"右键菜单上仍然是英文:{entry!r}(全部:{texts})"
    assert any(CJK.search(text) for text in texts), f"菜单里没有一个中文:{texts}"


def test_the_spec_packages_the_translation() -> None:
    """源码跑得对,打包产物里没有,是这一类缺陷最典型的下场。

    与 test_theme_resources.py 里那条样式表打包断言同一个理由:--self-check
    不检查菜单上的字,所以漏配只会表现为产物里一半中文一半英文,而开发机上
    一切正常。
    """
    spec = SPEC_PATH.read_text(encoding="utf-8")

    assert "qtbase_zh_CN.qm" in spec
    # 目标目录是运行时靠 Qt 自己报告的翻译路径找到它的地方;写错了就等于没打。
    assert "PySide6/translations" in spec
    assert "QT_TRANSLATION_DATAS" in spec
    assert "datas=RESOURCE_DATAS + QT_TRANSLATION_DATAS" in spec


def test_a_missing_translation_is_reported_rather_than_raised(monkeypatch, qapp) -> None:
    """菜单是英文的窗口仍然可用;打不开的窗口不可用。

    翻译文件缺失把一个措辞问题升级成可用性问题,是比英文菜单更坏的结果,所以
    安装路径返回 False 而不是抛异常。让它不声不响的那部分风险,由上面那条断
    菜单是中文的测试兜住。
    """
    monkeypatch.setattr(i18n, "find_translation_file", lambda: None)

    assert i18n.install_qt_translations(qapp) is False
