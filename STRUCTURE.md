# TaoMusicEdit 项目目录结构说明

本项目采用行业标准的 **src/ 布局**：所有源码包统一放在 `src/` 下，根目录只保留启动入口、
测试与项目配置，避免源码与配置文件、测试素材混在一起。

## 1. 目录总览

```
TaoMusicEdit/
├── run.py                  # ★ 统一启动入口（推荐从这里启动 Web UI）
├── pytest.ini              # pytest 配置：testpaths + pythonpath=src + 忽略规则
├── README.md               # 项目总览、功能与使用文档
├── STRUCTURE.md            # 本文件：目录结构与文件存放规范
├── .gitignore              # git 忽略规则
├── src/                    # ★ 全部源码包（Python 包根）
│   ├── detector/           # 视频颜色检测模块（可独立使用）
│   ├── trimmer/            # 视频裁剪模块（可独立使用）
│   └── webui/              # Web UI 后端（Flask）+ 前端
└── *.mp4                   # 测试 / 演示用视频素材（git 忽略，不提交）
```

## 2. 根目录文件职责

| 文件 | 职责 | 存放规范 |
| --- | --- | --- |
| `run.py` | 统一启动入口：将 `src/` 加入 `sys.path` 后导入 `webui.app` 并启动服务 | 仅保留启动引导，不写业务逻辑 |
| `pytest.ini` | pytest 配置：`testpaths` 限定收集范围，`pythonpath = src` 使测试可直接 `import detector/trimmer/webui` | 测试相关的公共配置放这里 |
| `README.md` / `STRUCTURE.md` | 项目与结构文档 | 文档只放根目录或对应包内 |
| `.gitignore` | 忽略规则（缓存、vendored 依赖、运行期产物、视频素材） | 新生成产物需及时补入 |

## 3. src/ 各包职责与子目录约定

### 3.1 src/detector —— 视频颜色检测模块

| 路径 | 功能定位 |
| --- | --- |
| `detector/__init__.py` | 包公共接口（重导出 `VideoColorDetector`、异常、颜色工具），`__all__` 定义 |
| `detector/__main__.py` | `py -m detector` 入口（需在 `src/` 下运行或 `PYTHONPATH=src`） |
| `detector/cli.py` | 命令行入口（推荐 `py src\detector\cli.py` 直跑，内置 sys.path 引导） |
| `detector/config.py` | `DetectorConfig` 全部可调参数 |
| `detector/core/` | **核心实现**：`algorithm.py`（检测算法）、`detector.py`（主入口）、`matcher.py`（匹配器）、`errors.py`（异常层级） |
| `detector/utils/` | **工具函数**：`ffmpeg.py`（抽帧/探测）、`color.py`（颜色转换与度量）、`logger.py`（日志） |
| `detector/tests/` | pytest 单元/集成测试（含 `conftest.py` 共享夹具） |
| `detector/README.md` | 模块使用文档 |

### 3.2 src/trimmer —— 视频裁剪模块

| 路径 | 功能定位 |
| --- | --- |
| `trimmer/__init__.py` | 包公共接口（重导出 `Trimmer`、`TrimmerConfig`、`OutputMode`、异常） |
| `trimmer/__main__.py` | `py -m trimmer` 入口 |
| `trimmer/cli.py` | 命令行入口（推荐 `py src\trimmer\cli.py` 直跑） |
| `trimmer/core/` | **核心实现**：`trimmer.py`（裁剪引擎）、`ffmpeg.py`（FFmpeg 封装）、`probe.py`（媒体探测）、`errors.py` |
| `trimmer/utils/` | **工具函数**：`validation.py`（参数校验）、`logger.py`（日志） |
| `trimmer/tests/` | pytest 单元/集成测试 |
| `trimmer/pyproject.toml` | 独立打包配置（`pip install ./src/trimmer` 可安装为 `trimmer` 命令） |
| `trimmer/README.md` | 模块使用文档 |

### 3.3 src/webui —— Web UI 后端 + 前端

| 路径 | 功能定位 |
| --- | --- |
| `webui/__init__.py` | 包标记（使 webui 可作为包导入） |
| `webui/app.py` | Flask 主应用：API 路由、任务编排、检测/裁剪集成（入口：`py run.py` 或 `py src\webui\app.py`） |
| `webui/jobs.py` | 异步任务管理器（后台线程 + SSE 进度 + 取消） |
| `webui/settings.py` | 设置持久化（读写 `src/webui/settings.json`） |
| `webui/templates/` | Jinja2 模板（`index.html`） |
| `webui/static/` | 前端资源：`css/style.css`、`js/`（`params.js` / `player.js` / `app.js`） |
| `webui/tests/` | pytest API 测试（`test_trim_api.py`） |
| `webui/requirements.txt` | Web UI 依赖清单 |
| `webui/_vendor/` | **vendored 依赖**（Flask 等）：运行期生成、git 忽略，由 `py -m pip install -r src/webui/requirements.txt --target src/webui/_vendor` 重建 |

## 4. 文件存放规范

1. **新代码入对应功能包**：检测逻辑入 `src/detector/`，裁剪逻辑入 `src/trimmer/`，Web 前后端入 `src/webui/`；禁止在根目录新增业务源码。
2. **子目录职责固定**：`core/` = 核心实现、`utils/` = 工具函数、`tests/` = 测试；新文件按职责归入对应子目录。
3. **vendored 依赖仅限 `src/webui/_vendor/`**：任何第三方包不得手工复制到其它位置；需要时通过 requirements.txt 重建。
4. **运行期产物不入库**：`src/webui/settings.json`（用户设置）、`src/webui/detect_cache.db*`（检测结果缓存 SQLite）、`src/webui/detect_cache.json*`（更早版本遗留的 JSON 缓存文件，迁移代码已移除，仅保留忽略规则以防误入库）、`__pycache__/`、`.pytest_cache/`、`*.egg-info/` 等均由 `.gitignore` 忽略，可安全删除重建。
5. **测试素材不提交**：仓库根目录的 `*.mp4` 测试视频为 git 忽略文件，勿 `git add`。

## 5. 导入约定

- **包内绝对导入**：`import detector.xxx` / `from trimmer.xxx import ...` / `from webui.xxx import ...`。
- **直接脚本运行**依赖各入口文件顶部的 sys.path 引导（如 `src/detector/cli.py` 自动将 `src/` 加入搜索路径），无需安装即可运行。
- **pytest** 通过 `pytest.ini` 的 `pythonpath = src` 解析包导入，测试文件无需手写 sys.path（webui 测试因依赖 `_vendor` 保留引导）。
- **`py -m detector/trimmer`**：需在 `src/` 目录下执行或设置 `PYTHONPATH=src`（Python 在解析 `__main__.py` 前已完成包发现）。
