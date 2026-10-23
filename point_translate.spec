# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

两个必须处理的坑：

1. PySide6 默认会把 QtWebEngine 一起收进来（光它就有 100MB+），
   而我们只用 QtCore / QtGui / QtWidgets。必须显式排除。

2. winsdk 和 uiautomation 是运行时动态加载的，PyInstaller 的
   静态分析发现不了。不声明的话会打出一个"能启动、一取词就崩"的包。

打包命令（不要直接 pyinstaller main.py，要用这个 spec）：
    .venv\\Scripts\\pyinstaller.exe point_translate.spec --noconfirm
"""

from PyInstaller.utils.hooks import collect_dynamic_libs

# 只声明真正用到的 winsdk 命名空间。
# 早先图省事用了 collect_submodules("winsdk")，结果把整个 winsdk
# 拖进来，_winrt.pyd 一个文件就 36MB。实测这 7 个模块就够了，
# 打包后自检 6/6 通过。
hidden = [
    # uiautomation 通过 comtypes 动态生成 COM 接口，静态分析看不到
    "uiautomation",
    "comtypes",
    "comtypes.client",
    "comtypes.gen",
    # winsdk 的各 WinRT 命名空间（用完即止，别贪多）
    "winsdk",
    "winsdk.system",
    "winsdk.windows.foundation",
    "winsdk.windows.globalization",
    "winsdk.windows.graphics.imaging",
    "winsdk.windows.media.ocr",
    "winsdk.windows.security.cryptography",
    "winsdk.windows.storage.streams",
    # 网络
    "requests",
    "urllib3",
    "certifi",
    "charset_normalizer",
    "idna",
]

# 明确排除用不到的大块头，直接决定最终体积
excludes = [
    # Qt 全家桶里我们用不到的部分（WebEngine 单独排除，它是最大的一块）
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuick3D",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSerialPort",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtUiTools",
    "PySide6.QtNetwork",
    "PySide6.QtPrintSupport",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebView",
    # Python 侧用不到的标准库大件
    "tkinter",
    "unittest",
    "pydoc",
    "doctest",
    "pdb",
    "lib2to3",
    "distutils",
    "setuptools",
    "pip",
    "test",
    # 科学计算类，我们没有依赖，但有时会被间接拖进来
    "numpy",
    "PIL",
    "matplotlib",
    "pandas",
    "scipy",
]

# 这些 DLL 是被 Qt 间接拖进来的，但纯 QWidgets 程序一个都用不到：
#   opengl32sw  软件 OpenGL 兜底渲染器，19MB
#   Qt6Quick / Qt6Qml / Qt6Pdf  只要不用 QML 就完全不需要
# PyInstaller 的 excludes 管不到二进制，只能在 COLLECT 阶段过滤掉。
_USELESS_DLLS = {
    "opengl32sw.dll",
    "d3dcompiler_47.dll",
    "qt6quick.dll",
    "qt6qml.dll",
    "qt6qmlmodels.dll",
    "qt6qmlworkerscript.dll",
    "qt6pdf.dll",
    "qt6opengl.dll",
    "qt6network.dll",
    "qt6svg.dll",
    "qt6designer.dll",
    "qt6sql.dll",
    "qt6test.dll",
    "qt6virtualkeyboard.dll",
}


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="指译 PointTranslate",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 不要黑色命令行窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # 自定义图标（没有就用默认）
    icon="appicon.ico" if __import__("os").path.exists("appicon.ico") else None,
)

def _keep(entry):
    """过滤掉用不到的 Qt DLL。"""
    name = entry[0].rsplit("\\", 1)[-1].rsplit("/", 1)[-1].lower()
    return name not in _USELESS_DLLS


coll = COLLECT(
    exe,
    [b for b in a.binaries if _keep(b)],
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PointTranslate",
)
