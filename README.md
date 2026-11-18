# 指译 PointTranslate

鼠标停在英文上约 1 秒，旁边自动弹出中文翻译。常驻系统托盘，不打断你正在做的事。

原本是为了看懂 Epic 商店和游戏库里的英文界面，但它对任何界面都有效。

**Windows 10 / 11 · Python 3.10+ · 无需任何 API Key**

## 特点

- **零配置开箱即用** —— 翻译源全是免密钥的公开接口，不用注册、不用填 token
- **UI Automation 优先** —— 桌面软件直接读控件的原始文字，零识别错误、1ms 级返回
- **OCR 兜底** —— UIA 拿不到时用 Windows 自带 OCR 识别画面，覆盖游戏和自绘界面
- **本地缓存** —— 翻过的词存 SQLite，第二次是 0ms 命中
- **鼠标穿透** —— 气泡看得见但点不到，不会挡住你操作
- **智能跳过** —— 中文界面、文件名、路径、版本号不会浪费网络请求

---

## 快速开始

```powershell
git clone https://github.com/<your-name>/point-translate.git
cd point-translate

# 建虚拟环境并装依赖
uv venv .venv --python 3.11
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt

# 运行
.\.venv\Scripts\python.exe main.py
```

没装 `uv` 的话用标准 venv：

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

启动后没有主窗口，只在**右下角托盘**出现一个蓝色"译"字图标。这就对了。
首次运行会在当前目录自动生成 `config.json`（默认配置）和 `cache.sqlite3`（翻译缓存）。

想边用边看调试信息（控制台会打印每次翻译的内容和耗时）：

```powershell
.\.venv\Scripts\python.exe main.py --verbose
```

---

## 怎么用

1. 把鼠标**停**在一段英文上（别动，约 1 秒）
2. 鼠标旁边弹出气泡，显示中文
3. 鼠标一移开，气泡自动收起

托盘图标操作：

| 操作 | 效果 |
|---|---|
| 左键单击 | 暂停 / 恢复翻译 |
| 右键 → 暂停翻译 | 同上，勾选状态表示已暂停 |
| 右键 → 查看缓存统计 | 看已经缓存了多少条、命中多少次 |
| 右键 → 清空翻译缓存 | 缓存出问题时重置 |
| 右键 → 打开配置文件 | 用记事本打开 `config.json` |
| 右键 → 退出 | 完全关闭程序 |

---

## 它怎么工作的

鼠标悬停后，程序按两条路径取词，谁先拿到有效结果就用谁：

**路径一：UI Automation（优先）**
直接问 Windows 无障碍接口"鼠标底下这个控件写着什么"。
拿到的是**原始字符串，零识别错误**，而且不用截图，1 毫秒级返回。
Epic 启动器、资源管理器、大多数桌面软件都走这条路。

**路径二：截图 + Windows OCR（兜底）**
UIA 拿不到文字时（游戏画面、网页、自绘界面），
就截取鼠标周围一小块图，交给 Windows 自带的 OCR 引擎识别。
不联网、不装额外模型，但需要系统装了对应语言的 OCR 包。

取到文字后交给翻译层，按内容类型选源：

| 内容 | 走哪个源 | 实测耗时 |
|---|---|---|
| 单词（无空格、<24 字符） | 金山词霸 → 有道词典 | **60~200 ms** |
| 句子 | 必应翻译 → MyMemory | 0.7~1.5 s |
| 翻译过的内容 | 本地 SQLite 缓存 | **0 ms** |

单个源失败会自动换下一个，不会因为某个接口抽风就整个罢工。

---

## 配置文件 `config.json`

改完保存，**重启程序**生效。

```jsonc
{
  "hover_seconds": 1.0,      // 鼠标停多久触发。嫌太灵敏就调大，比如 1.5
  "poll_interval": 0.08,     // 鼠标位置检测间隔，一般不用改
  "move_tolerance": 4,       // 移动多少像素算"动了"，手抖的话调大
  "min_text_length": 2,      // 少于几个字符不翻译
  "cooldown_seconds": 1.5,   // 触发一次后歇多久再重新判定，防止同一处反复翻译

  "use_uia": true,           // UIA 取词
  "use_ocr": true,           // OCR 兜底
  "ocr_width": 420,          // OCR 截图范围，字大就调大
  "ocr_height": 90,

  "font_size": 13,           // 气泡字号
  "opacity": 0.96,           // 气泡透明度 0~1
  "max_width": 460,          // 气泡最大宽度
  "offset_x": 18,            // 气泡相对鼠标的偏移
  "offset_y": 20,
  "auto_hide_seconds": 6.0,  // 鼠标移开后多久收起气泡

  "enabled": true            // 总开关
}
```

---

## 建议做的一步：装英文 OCR 包

你的系统目前**只装了中文 OCR**，没装英文的：

```
is_language_supported('en-US')      = False
is_language_supported('zh-Hans-CN') = True
```

中文引擎也能硬认英文，但会犯这些错：

| 实际文字 | 中文引擎识别成 |
|---|---|
| Install | `lnstall` |
| Sign In | `Sign 《 n` |
| not available | `n Ot available` |

程序里已经加了纠错规则来补救，但**装了英文包就没有这些问题**：

1. 打开 **设置 → 时间和语言 → 语言和区域**
2. 点 **添加语言**，搜索 `English (United States)`，安装
3. 回到语言列表，点 English 右边的 **⋯ → 语言选项**
4. 在"语言功能"里找到 **光学字符识别 (OCR)** 并安装
5. 重启指译

装完之后再跑一次 `_smoke.py`，会看到 `en-US True`。

> 注意：这只影响 OCR 兜底路径。如果你的主要目标是 Epic 启动器，
> 它走的是 UIA，不受这个影响，装不装都能用。

---

## 关于游戏

- **独占全屏的游戏截不到图**，OCR 路径无效。游戏请用"无边框窗口"模式。
- 游戏画面是 DirectX 自绘的，UIA 也读不到文字，所以游戏完全依赖 OCR。
- 好消息：Steam / Epic 上大多数游戏菜单本身有中文选项，
  优先在游戏设置里找 `Language`。这个工具更适合补那些没中文的老游戏。

---

## 打包成 exe

仓库里不含编译产物（体积太大），需要自己打。一条命令搞定：

```powershell
powershell -ExecutionPolicy Bypass -File build-release.ps1
```

这个脚本会依次完成：停掉运行中的实例 → 按 spec 构建 exe →
补上 `start.bat` → 清掉本机运行时数据 → 压成规范的 zip。

> 为什么不直接敲 `pyinstaller point_translate.spec`：`--clean` 会
> **整个删掉 dist 目录**，手工放进去的 `start.bat` 每次都会丢。
> 步骤固化在脚本里才不会漏。

生成结果：

```
dist\PointTranslate\              解压后的运行目录
dist\PointTranslate-v<版本>-win64.zip   发布用的压缩包（约 40 MB，版本取自 pyproject.toml）
```

### 分步说明

```
dist\PointTranslate\
├─ 指译 PointTranslate.exe    双击即可运行
├─ start.bat                   备用启动脚本
├─ config.json                 首次运行自动生成
├─ cache.sqlite3               翻译缓存
└─ _internal\                  Qt / Python 运行时，别删
```

**总体积约 112 MB**。第一次运行稍慢（要解包 Qt），之后正常。

> exe 只有 3.5 MB，但**不能单独拷出来用** —— 旁边的 `_internal` 必须一起带着。
> 想搬到别处就整个 `PointTranslate` 文件夹一起复制。

`start.bat` 故意写成**纯 ASCII**：cmd.exe 是按系统 OEM 代码页（中文系统是 GBK）
读取 .bat 文件的，里面写中文注释会变成乱码命令直接执行失败。
exe 名字带中文，在批处理里也不适合直接写，所以它是用通配符查找的。

**不要直接 `pyinstaller main.py`**，必须用 spec 文件，原因见下面。

### 打包时踩到的坑

已经都处理好了，这里记下来免得以后重踩：

**1. PySide6 会把 QtWebEngine 一起塞进来（多 100MB+）**
我们只用 QtCore / QtGui / QtWidgets。spec 里排除了一长串用不到的 Qt 模块，
并额外过滤掉 `opengl32sw.dll`（19.7MB 的软件渲染兜底）和
`Qt6Quick` / `Qt6Qml` / `Qt6Pdf`。这一项一共省了 41MB。

**2. `winsdk` 和 `uiautomation` 是动态加载的，静态分析发现不了**
PyInstaller 扫不到它们，不写进 `hiddenimports` 的话会打出一个
"能启动、一取词就崩"的空壳。必须在 spec 里手动声明。

**3. 冻结后 `__file__` 指向临时目录**
原始代码用 `Path(__file__).parent` 定位配置和缓存，打包后这指向
PyInstaller 的 `_MEIxxxx` 临时目录，**退出即删** —— 会导致每次运行
缓存都白攒、配置存不住。现在检测 `sys.frozen`，改用 exe 所在目录。

**4. 脚本文件不能带中文（编码坑，踩了两次）**
- `.bat`：cmd.exe 按系统 OEM 代码页（中文系统是 GBK）读文件，
  写中文注释会变成乱码命令直接执行失败。
- `.ps1`：Windows PowerShell 5.1 对无 BOM 的 UTF-8 也按 GBK 读，
  中文会让解析器报"字符串缺少终止符"。

所以 `start.bat` 和 `build-release.ps1` 一律**纯 ASCII**。
exe 名字带中文，批处理里用通配符 `*PointTranslate.exe` 查找而不是写死。

**5. `Compress-Archive` 生成的 zip 不合规范**
它把条目路径写成反斜杠 `PointTranslate\start.bat`，违反 ZIP 规范
（APPNOTE 要求正斜杠）。Windows 资源管理器能容忍，但 7-Zip、macOS、
Linux 的解压工具可能解出名字里带反斜杠的畸形文件。
所以改用 Python `zipfile` 生成（`_make_release_zip.py`），它始终用正斜杠，
并会为中文文件名正确设置 UTF-8 标志位。

### 打包后怎么验证

exe 是 `console=False`（没有黑色命令行窗口），所以自检结果会写成文件
（固定写在 **exe 旁边**，不受启动时工作目录影响）：

```powershell
Start-Process 'dist\PointTranslate\指译 PointTranslate.exe' -ArgumentList '--selftest' -Wait
Get-Content 'dist\PointTranslate\selftest.txt'
```

6 项全 `[OK]` 就说明打包完整。注意**必须用 `Start-Process -Wait`**：
这种 exe 从命令行启动会立即返回，直接 `& .\xxx.exe --selftest`
会因为还没写完文件就去看而误判成失败。

### 打包时的正常警告

```
WARNING: Library UIAutomationClient_VC140_X64.dll required via ctypes not found
```

可以忽略。`uiautomation` 会去找它自带的 VC140 版本 DLL，
找不到就回退用系统自带的 UIAutomationCore。实测功能正常。

---

## 出问题时

**完全没反应**
- 先确认没在托盘菜单里勾着"暂停翻译"
- 用 `--verbose` 启动，看控制台有没有输出
- 确认鼠标指针**在动**的时候不会触发（这是设计如此），要停住约 1 秒

**托盘没有图标**
Windows 可能把它折叠了。点任务栏的 `^` 展开，把图标拖到任务栏上固定住。

**气泡一闪就没了 / 显示"这里没有可翻译的文字"**
说明取到了东西但没意义（占位符、单个字符）。换个位置试试。

**气泡挡住了我要点的按钮**
不会。气泡设置了鼠标穿透，看得见但点不到，鼠标事件会穿透到下面的窗口。
如果你遇到点不动的现象，请反馈。

**翻译结果是英文的**
说明命中的源没译出来。在托盘里"清空翻译缓存"再试。
也可以改 `config.json` 里的 `providers` 顺序。

**翻译错了**
试试在设置里把 `use_uia` 关掉强制走 OCR，或者反过来。
`--verbose` 会打印每次用的是哪个源（`[bing]` / `[iciba]` / `[cache]`）。

---

## 文件说明

```
point-translate/
├─ main.py               入口：托盘、开关、自检、把各层串起来
├─ config.py             配置读写（含打包后的路径处理）
├─ winutil.py            Windows 底层：DPI 感知、截图、鼠标穿透
├─ overlay.py            气泡窗口（鼠标穿透 + 自动定位）
├─ triggers.py           悬停检测线程：什么时候该翻译
├─ translate.py          翻译层：多源回退 + SQLite 缓存 + OCR 纠错
├─ extractors/
│  ├─ uia.py             UI Automation 取词
│  └─ ocr.py             Windows OCR 取词
├─ _smoke.py             自检：各层是否正常
├─ _integration.py       集成测试：悬停触发链路
├─ point_translate.spec  PyInstaller 打包配置
├─ build-release.ps1     一键构建发布包
├─ _make_release_zip.py  生成符合 ZIP 规范的发布压缩包
├─ start.bat             启动脚本（纯 ASCII）
├─ requirements.txt      依赖
└─ LICENSE               MIT
```

## 自检与测试

```powershell
# 各层自检（OCR 引擎、截屏、翻译源、UIA）
.\.venv\Scripts\python.exe _smoke.py

# 集成测试：自己移动鼠标验证悬停触发（注意：会动你的鼠标指针）
.\.venv\Scripts\python.exe _integration.py
```

---

## 已知限制

1. **首次翻译句子有约 0.6 秒额外延迟** —— 需要先取必应翻译的鉴权 token。
   程序启动时已在后台预热，正常使用感知不到。
2. **中文界面会被跳过** —— 界面文字里只要掺了较多中文就不翻译，避免做无用功。
3. **文件名和路径会被跳过** —— `winutil.py`、`C:\Users\...` 这类内容翻译没有意义。
4. **中文 OCR 引擎认英文有错字** —— 见上面"建议做的一步"。
5. 翻译源用的是公开的免费接口，**依赖它们的可用性**，随时可能变动或限流。
   所有结果都有本地缓存，常用的词不会反复请求。

---

## 免责与声明

- 本项目仅用于**帮助阅读英文界面**，翻译结果由第三方公开接口提供，不保证准确。
- 翻译内容会以文本形式发送到对应的翻译服务（金山词霸 / 有道 / 必应 / MyMemory）。
  **请勿在涉及密码、密钥、个人隐私的界面上使用本工具。**
- 翻译源均为各服务公开的网页接口，本项目不存储、不转发你的翻译内容，
  但你与这些服务之间仍受各自的服务条款约束。
- 本项目与 Epic Games、Microsoft、金山、有道、必应均无关联。

---

## License

[MIT](LICENSE) © 2026 ppgod-787

你可以自由使用、修改、分发，包括商用，只需保留版权声明。
