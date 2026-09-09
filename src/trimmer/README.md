# trimmer —— 基于 FFmpeg 的高精度视频裁剪工具

一个功能完整、可直接被其他项目调用（pip 安装）的视频裁剪 Python 模块。
以 FFmpeg 为核心处理引擎，支持按**帧序号**或**时间戳**指定保留**起点**，
并可选指定**终点**（区间裁剪，保留 `[起点, 终点)` 区间；终点缺省时保留到片尾），
默认采用流复制快速裁剪，可选重编码（自动匹配原视频参数）
并支持 **GPU 硬件加速**（自动检测、失败自动回退软件编码）。

## 特性

- **两种裁剪方式**：帧序号（`-f` / `-F`）或时间戳（`-t` / `-T`）指定**保留起点 / 终点**，
  起点与终点的方式可独立选择（内部统一换算为时间戳），二者各自严格二选一并进行范围校验
- **区间裁剪（可选终点）**：终点缺省时保留到片尾（与原有行为完全一致）；
  提供终点时保留 `[起点, 终点)` 区间，终点须大于起点
- **快速裁剪**：默认流复制（`-c copy`）不重编码，秒级完成
- **参数自动匹配**：重编码时自动匹配原视频的编码器、码率、分辨率、帧率、像素格式及音频参数
- **GPU 加速**：自动检测 `nvenc / qsv / amf / videotoolbox` 等硬件编码器并优先使用，
  GPU 执行失败时自动回退到软件编码
- **四种输出模式**：完整视频、无声视频、纯音频、分离音视频（区间裁剪全部支持）
- **安全机制**：输出默认在原目录生成 `<原名>_trim<扩展名>`；覆盖需确认；
  禁止输出路径与输入文件相同（防误覆盖原文件）
- **完善错误处理**：文件不存在、格式不支持、参数错误、缺流、FFmpeg 缺失/失败等均有明确错误码
- **进度反馈**：命令行输出动态进度条；Python API 提供进度回调
- **纯标准库**：无第三方依赖，仅需系统安装 FFmpeg

## 目录结构

```
trimmer/
├── __init__.py            # 包入口，导出主要类
├── __main__.py            # python -m trimmer 入口
├── cli.py                 # 命令行参数解析与主流程
├── pyproject.toml         # 打包配置（可 pip 安装）
├── core/                  # 核心模块
│   ├── errors.py          # 异常体系（错误码 + 退出码）
│   ├── probe.py           # ffprobe 媒体探测与编码参数匹配
│   ├── ffmpeg.py          # FFmpeg 封装：可执行文件定位、版本/编码器探测、命令执行与进度解析
│   └── trimmer.py         # 裁剪引擎：校验、命令构建、执行编排
├── utils/
│   ├── validation.py      # 参数严格校验
│   └── logger.py          # 日志
├── examples/
│   └── usage_example.py   # Python API 使用示例
└── tests/                 # 单元测试 + 集成测试
```

## 安装

方式一：直接使用（无需安装）

```bash
python -m trimmer --help          # 在仓库根目录下执行
```

方式二：pip 安装（可被其他项目 import）

```bash
pip install ./trimmer
trimmer --help                    # 安装后获得 trimmer 命令
```

依赖：Python ≥ 3.8、FFmpeg（含 ffprobe），无需任何第三方 Python 包。

## 命令行用法

```bash
trimmer INPUT (-t 时间戳 | -f 帧序号) [-T 终点时间戳 | -F 终点帧序号] [选项]
```

### 参数说明

| 参数 | 说明 |
| --- | --- |
| `INPUT` | 输入视频路径（必填，支持绝对/相对路径） |
| `-t, --timestamp SEC` | 裁剪起始时间戳（秒，浮点数，如 `24.9`），保留此后内容，与 `--frame` 二选一 |
| `-f, --frame N` | 裁剪起始帧序号（从 1 开始），保留第 N 帧及之后内容，与 `--timestamp` 二选一 |
| `-T, --end-timestamp SEC` | 裁剪终点时间戳（秒，浮点数），保留 `[起点, 终点)` 区间；与 `--end-frame` 二选一，缺省 = 保留到片尾 |
| `-F, --end-frame N` | 裁剪终点帧序号（从 1 开始），保留 `[起点, 终点)` 区间；与 `--end-timestamp` 二选一，缺省 = 保留到片尾 |
| `-o, --output FILE` | 自定义输出路径（默认原目录 `<原名>_trim<扩展名>`） |
| `-d, --output-dir DIR` | 自定义输出目录（默认与原视频同目录） |
| `-s, --suffix SUFFIX` | 默认文件名后缀（默认 `_trim`） |
| `-y, --force` | 输出已存在时直接覆盖，不再询问 |
| `-r, --reencode` | 重编码输出（默认流复制快速裁剪） |
| `-c, --video-codec CODEC` | 指定视频编码器（如 `libx264` / `hevc_nvenc`） |
| `--audio-codec CODEC` | 指定音频编码器（如 `aac` / `libmp3lame`） |
| `--hw-accel {auto,none,force}` | GPU 加速策略：`auto` 自动检测优先使用（默认）/ `none` 关闭 / `force` 强制 |
| `--no-audio, --video-only` | 仅输出视频流（无声视频） |
| `--audio-only` | 仅输出音频流 |
| `--split` | 分离输出视频文件与音频文件 |
| `--verbose` | 输出详细调试日志 |
| `-V, --version` | 显示版本号 |

终点规则：`--end-timestamp` 与 `--end-frame` 不可同时提供；终点（换算后）必须**大于**起点
且不超过视频时长 / 总帧数；起点方式与终点方式可不同（如起点用帧序号、终点用时间戳，
内部统一换算为时间戳执行）；不带终点参数时行为与仅起点裁剪完全一致。

### 示例

```bash
# 从 24.9 秒开始保留（流复制，快速，裁掉开头）
trimmer input.mp4 -t 24.9

# 从第 900 帧开始保留（30fps 下约 29.97 秒处）
trimmer input.mp4 -f 900

# 区间裁剪：保留 [24.9s, 3600s)
trimmer input.mp4 -t 24.9 -T 3600

# 帧序号区间：保留 [第 735 帧, 第 108000 帧)
trimmer input.mp4 -f 735 -F 108000

# 重编码并启用 GPU 加速（自动检测 GPU，无 GPU 时自动回退软件编码）
trimmer input.mp4 -t 24.9 -r --hw-accel

# 输出无声视频
trimmer input.mp4 -t 24.9 --no-audio

# 仅输出音频（aac 输入输出 .m4a）
trimmer input.mp4 -t 24.9 --audio-only

# 分离输出视频与音频到指定目录
trimmer input.mp4 -t 24.9 --split -d out/
```

### 输出文件命名

| 模式 | 默认输出 |
| --- | --- |
| 默认 / 无声视频 | `<原名>_trim<原扩展名>` |
| 纯音频 | `<原名>_trim.m4a`（按音频编码推断扩展名） |
| 分离 | `<原名>_trim_video<原扩展名>` + `<原名>_trim_audio.m4a` |

## 重编码与 GPU 加速

- **流复制（默认）**：`-c:v copy -c:a copy`，不重新编码，速度快、画质无损，但保留起点会吸附到关键帧。
- **重编码（`-r`）**：自动探测并匹配原视频的编码器（h264→libx264、hevc→libx265 等）、
  码率、帧率、分辨率、像素格式，以及音频编码器、码率、采样率、声道数，确保输出与原视频参数最大一致。
- **GPU 加速（`--hw-accel auto` 默认开启）**：分两步自动适配不同显卡厂商：

  1. **识别显卡厂商**：通过操作系统探测 GPU（Windows 下用 PowerShell CIM / wmic，
     Linux 下用 `nvidia-smi` / `lspci`，macOS 下用 `system_profiler`），
     区分 NVIDIA / AMD / Intel；
  2. **挑选硬件编码器**：优先选择与显卡厂商匹配的编码器并添加 `-hwaccel auto`：

     | 显卡厂商 | 优先编码器 |
     | --- | --- |
     | NVIDIA | `h264_nvenc` / `hevc_nvenc` |
     | AMD | `h264_amf` / `hevc_amf` |
     | Intel | `h264_qsv` / `hevc_qsv` |
     | macOS（Apple） | `h264_videotoolbox` / `hevc_videotoolbox` |
     | 无法识别 | 按通用优先级尝试（nvenc → qsv → amf → videotoolbox） |

- **失败逐级回退**：若首选 GPU 编码器执行失败（如驱动缺失、驱动版本过旧），
  会自动依次尝试下一个可用硬件编码器，全部失败后再回退到软件编码并给出警告——
  因此 AMD 用户（无 NVIDIA 驱动）不会被 nvenc 阻塞，NVIDIA/Intel 双显卡等场景也能正确选型。
- `--hw-accel none`：完全关闭 GPU；`--hw-accel force`：找不到硬件编码器时直接报错，
  且 GPU 执行失败不再自动回退，便于排查硬件编码问题。

## Python API

```python
from trimmer import OutputMode, Trimmer, TrimmerConfig

# 按时间戳从 24.9s 开始保留（流复制），支持进度与覆盖确认回调
config = TrimmerConfig(input_path="input.mp4", timestamp=24.9)
result = Trimmer(config).run(
    progress_cb=lambda pct, sec: print(f"\r{pct:.1f}%", end=""),
    confirm_cb=lambda path: input(f"覆盖 {path}? [y/N] ") == "y",
)
print(result.output_files)      # ["...input_trim.mp4"]

# 区间裁剪：保留 [24.9s, 3600s)（终点缺省时保留到片尾）
result = Trimmer(TrimmerConfig(
    input_path="input.mp4", timestamp=24.9, end_timestamp=3600.0,
)).run()
print(result.cut_duration)      # 3575.1（end - start）
print(result.end_timestamp)     # 3600.0

# 按帧序号从第 900 帧开始保留 + 重编码 + GPU 加速
result = Trimmer(TrimmerConfig(
    input_path="input.mp4", frame=900, reencode=True, hw_accel="auto",
)).run()

# 纯音频 / 分离模式（区间裁剪同样支持）
Trimmer(TrimmerConfig(input_path="input.mp4", timestamp=5.0,
                      output_mode=OutputMode.AUDIO_ONLY)).run()
Trimmer(TrimmerConfig(input_path="input.mp4", timestamp=5.0, end_timestamp=60.0,
                      output_mode=OutputMode.SPLIT)).run()
```

`TrimmerConfig` 终点字段：`end_timestamp`（`float | None`）与 `end_frame`（`int | None`）
二选一、均可缺省（缺省 = 保留到片尾）；`TrimmerResult` 相应提供
`end_timestamp` / `end_frame` 与按 `end - start` 计算的 `cut_duration`。

更完整的示例见 `trimmer/examples/usage_example.py`。

## 错误码

| 错误码 | 含义 |
| --- | --- |
| `FFMPEG_NOT_FOUND` | 未找到 ffmpeg / ffprobe |
| `INPUT_NOT_FOUND` | 输入文件不存在 |
| `UNSUPPORTED_FORMAT` | 文件格式不受支持或无法解析 |
| `NO_STREAM` | 输入缺少当前输出模式所需的音/视频流 |
| `VALIDATION_ERROR` | 参数校验失败（如未提供或同时提供裁剪参数、终点 ≤ 起点、终点/起点超出时长或帧数等） |
| `OUTPUT_EXISTS` | 输出文件已存在且未确认覆盖 |
| `OVERWRITE_INPUT` | 输出路径与输入文件相同，禁止覆盖原文件 |
| `FFMPEG_EXECUTION_ERROR` | ffmpeg 执行失败（含流复制区间双端吸附后无可输出内容） |

## 裁剪精度说明

- **时间戳模式**：`-t <时间戳>` 作为保留起点（裁剪掉之前的开头内容）。流复制时起点会吸附到
  最近的关键帧（误差 ≤ 一个关键帧间隔，通常 1~2 秒内）；重编码时逐帧解码，误差 ≤ 一帧。
- **帧序号模式**：起始时间 = `(帧序号 - 1) / 帧率`（如 30fps 下第 150 帧 ≈ 4.967 秒），
  保留第 N 帧及之后的帧；重编码时可精确到帧，流复制时同样受关键帧吸附影响。
- **区间裁剪（终点）**：FFmpeg ≥ 4.4 时使用输入侧 `-ss <start> -to <end>`——两者均在
  输入时间轴上取绝对位置，起点吸附不影响终点，流复制下终点为包级截断（接近精确）、
  重编码下帧精确；旧版 FFmpeg 自动回退输出侧 `-t <end - start>`，此时终点随起点
  吸附整体前移。流复制双端吸附后区间为空时明确报错，不会输出空文件。
- **音视频同步**：始终对视频与音频使用同一 `-ss` 起点，并通过 `-avoid_negative_ts make_zero`
  规避起始时间戳偏移，不会引入新的音画不同步。
- **VFR 视频**：帧序号 ↔ 时间戳换算采用平均帧率近似，区间端点建议优先使用时间戳方式。

## 支持格式

输入：MP4、AVI、MKV、MOV、WEBM、FLV、M4V、MPEG/MPG、TS、MTS/M2TS、WMV、3GP/3G2、OGV 等（扩展名白名单校验，
实际以 FFmpeg 能力为准）。输出容器默认沿用输入格式。

## 测试

```bash
python -m pytest trimmer/tests -q
```

共 **106 个测试全部通过**（`python -m pytest trimmer/tests -q`）。

包含单元测试（校验、CLI 解析、命令构建含区间 `-to` / `-t` 回退分支、输出路径）
与集成测试（生成 10 秒测试视频，验证时间戳/帧序号/区间裁剪时长/纯音频/无声/分离/
覆盖确认/错误处理，需要 FFmpeg，缺失时自动跳过）。
