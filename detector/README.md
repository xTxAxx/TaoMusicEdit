# detector — 视频颜色检测模块

基于 **Python + OpenCV + FFmpeg** 的可复用视频检测模块，采用 **「反向跳帧 + 局部细化」** 算法，
检测视频前 40 秒内四个指定位置的颜色是否为 `#F7F10F`（置信度阈值 0.97），并精确定位颜色区域的**结尾帧（最后一个命中帧）**。

模块封装良好、接口规范，可直接 `import detector` 集成到其他项目。

---

## 1. 功能特性

- **反向跳帧 + 局部细化**：先以 5s 大步长反向抽帧快速定位颜色区域，再以 1s 小步长细化，最后逐帧扫描精确定位**结尾帧（最后一个命中帧）**；全程只解码约 40~50 帧，**不整片解码**。
- **四点联合判定**：默认检测 `(20,20) (1900,20) (20,1060) (1900,1060)` 四个角点，四角同时命中才算匹配。
- **坐标自动适配**：检测点坐标按视频分辨率等比缩放（默认参考 1920×1080），支持任意分辨率与编码。
- **颜色置信度 + 可配置容差**：提供 0~1 的连续置信度，严格阈值 0.97，并开放颜色容差接口以兼容有损编码的颜色偏差。
- **FFmpeg 精确抽帧**：通过 `ffmpeg -ss` 输入定位 + 单帧解码抽帧，快速、精确；提供 OpenCV 提取器作为回退。
- **进度回调与日志**：分阶段进度回调（0~1），结构化日志记录关键过程。
- **完善的异常体系**：统一捕获 `DetectorError`，细分文件不存在 / 格式不支持 / FFmpeg 失败等异常。
- **测试完备**：85 个单元 / 集成 / 性能测试全部通过。

---

## 2. 安装与依赖

### 2.1 环境要求

- Python 3.8+
- [FFmpeg](https://ffmpeg.org/)（含 `ffmpeg` 与 `ffprobe`，加入 PATH）
- Python 依赖：

```bash
pip install opencv-python numpy
# 运行测试还需要
pip install pytest
```

### 2.2 目录结构

```
detector/
├── __init__.py            # 模块公共接口（VideoColorDetector、异常、颜色工具等）
├── __main__.py            # `py -m detector <视频>` 命令行入口（无需安装）
├── cli.py                 # 命令行入口（py detector/cli.py <视频>）
├── config.py              # DetectorConfig：全部可配置参数
├── core/                  # 核心检测算法
│   ├── algorithm.py       # 反向跳帧 + 局部细化（纯逻辑，可独立测试）
│   ├── detector.py        # VideoColorDetector 主入口，编排整条流水线
│   ├── matcher.py         # 帧级四点颜色匹配器（坐标适配 + 置信度计算）
│   └── errors.py          # 异常层级
├── utils/                 # 工具函数
│   ├── ffmpeg.py          # ffprobe/ffmpeg 抽帧、两种帧提取器
│   ├── color.py           # 颜色转换、置信度度量
│   └── logger.py          # 统一日志
├── tests/                 # 单元 / 集成 / 性能测试
├── examples/
│   └── usage_example.py   # 使用示例
└── README.md
```

---

## 3. 命令行调用（推荐，无需写代码）

从其他项目直接调用，只传入视频文件路径即可得到 `frame` 和 `time`：

```bash
py detector\cli.py video.mp4
# 输出: frame=734 time=24.466666666666665
```

- 未命中目标颜色时向 stderr 输出提示，退出码为 1。
- 诊断日志统一走 stderr，不污染 stdout，便于脚本解析结果。

常用选项：

```bash
py detector\cli.py video.mp4 --target '#F7F10F'   # 指定目标颜色（#RRGGBB 或 R,G,B）
py detector\cli.py video.mp4 --threshold 0.9      # 置信度阈值
py detector\cli.py video.mp4 --tolerance 15       # 颜色容差
py detector\cli.py video.mp4 --points 20,20 300,300  # 自定义检测点（数量=坐标个数）
py detector\cli.py video.mp4 --points 100,100 --no-scale-points  # 关闭坐标缩放
py detector\cli.py video.mp4 --detail             # 详细诊断（写入 stderr）
py detector\cli.py video.mp4 --extractor opencv   # 使用 OpenCV 提取器
```

等价的模块入口（无需安装，在项目根目录下运行）：

```bash
py -m detector video.mp4
```

---

## 4. Python API 调用

```python
from detector import VideoColorDetector

detector = VideoColorDetector()
frame, time = detector.detect("input1.mp4")  # 直接解构出帧序号与时间戳

if frame is not None:
    print(f"目标帧序号: {frame}")
    print(f"目标时间戳: {time:.6f} 秒")
else:
    print("未检测到目标颜色")
```

需要详细诊断信息时（各点置信度、定位区间、阶段统计等调试数据）：

```python
result = VideoColorDetector().detect("input1.mp4", detail=True)
if result.detected:
    print(f"整体置信度: {result.details.confidence:.4f}")
    for p in result.details.point_matches:
        print(f"点({p.x},{p.y}) RGB={p.rgb} conf={p.confidence:.4f} matched={p.matched}")
```

运行示例脚本（项目根目录下）：

```bash
py detector\examples\usage_example.py            # 使用默认视频 input1.mp4
py detector\examples\usage_example.py input2.mp4 # 指定视频
```

运行测试：

```bash
py -m pytest
```

---

## 5. 算法说明（反向跳帧 + 局部细化）

目标：在视频前 `search_window`（默认 40s）内，定位**四点同时命中目标颜色的结尾帧（offset，即最后一个命中帧）**。

| 阶段 | 说明 |
| --- | --- |
| ① 初始阶段（反向跳帧） | 从 `initial_start`（默认 35s）起，按 `coarse_step`（默认 5s）向 0 **反向**抽帧，得到粗粒度采样序列（并补扫窗口上端，覆盖 `[0, 40]`） |
| ② 定位阶段 | 找最后一个「匹配 → 不匹配」的过渡，得到粗区间 `(t_hit, t_next]`，其中 `t_hit` 满足、`t_next` 不满足 |
| ③ 细化阶段 | 在 `(t_hit, t_next]` 内以 `fine_step`（默认 1s）再次检测，得到更窄区间 `(t_hit_f, t_next_f]` |
| ④ 精确阶段 | 在 `(t_hit_f, t_next_f]` 内逐帧扫描，返回**最后一个**命中帧（帧序号 + 时间戳 + 各点置信度） |

- 若粗网格未命中（颜色区域极窄或位于 `(initial_start, search_window]` 缝隙中），
  自动执行**整窗 1s 细扫兜底**，保证窗口内不漏检。
- 若颜色区域延续到窗口末端，精确扫描会向前扩展一个 `fine_step` 以覆盖最后一个命中帧。
- 全程仅解码约 40~50 帧：粗扫约 9 帧、细化约 5 帧、精确约 30 帧，远小于整片帧数（如 10654 帧）。

以 `input1.mp4` 为例的检测轨迹：

```
coarse   反向抽帧 t=35(否)→30(否)→25(否)→20(命中)→15(命中)→10(命中)→5(否)→0(否) → 补扫 40(否)
locate   定位粗区间 (20.00, 25.00]
fine     细化 t=21(命中)→22(命中)→23(命中)→24(命中)→25(否) → 细化区间 (24.00, 25.00]
precise  逐帧 frame 720~733(命中) → 734(最后一个命中) → 目标帧 frame=734, time=24.466667s
```

---

## 6. 颜色置信度度量与容差

单个像素与目标颜色的置信度定义为 **「欧氏距离 + 可配置容差」**：

```
dist     = ‖pixel − target‖₂            （RGB 欧氏距离）
max_dist = √3 × 255                     （RGB 空间最大距离）
conf     = 1.0                          若 dist ≤ tolerance
           1 − (dist−tolerance)/(max_dist−tolerance)   否则
```

- 四点的**整体置信度**取四点的最小值，且要求**四点全部** `conf ≥ threshold` 才算命中。
- `confidence_threshold`（默认 `0.97`）：严格匹配阈值。
- `color_tolerance`（默认 `10.0`）：颜色容差范围，**可配置接口**。
  真实视频为有损编码，目标区域像素约为 `(245,254,19)`，距 `#F7F10F` 欧氏距离约 13~16；
  默认容差 10 时区域置信度稳定在 **0.987~0.993**（≥0.97），非目标区约 0.19，判别清晰。
  若将容差设为 0（严格精确匹配），有损编码下通常无法达到 0.97/0.999 阈值 —— 这正是容差参数的意义。

```python
from detector import hex_to_rgb, color_confidence

rgb = hex_to_rgb("#F7F10F")           # (247, 241, 15)
conf = color_confidence((245, 254, 19), rgb, tolerance=10.0)   # ≈ 1.0
```

---

## 7. API 文档

### 7.1 `DetectorConfig`（`detector/config.py`）

全部可配置参数（dataclass），支持 `with_overrides(**kwargs)` 局部覆盖、`validate()` 校验、`to_dict()` 导出。

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `target_color` | `(int,int,int)` | `(247,241,15)` | 目标颜色 RGB（`#F7F10F`） |
| `confidence_threshold` | `float` | `0.97` | 置信度阈值（[0,1]） |
| `color_tolerance` | `float` | `10.0` | 颜色容差（RGB 欧氏距离） |
| `points` | `((x,y),...)` | `((20,20),(1900,20),(20,1060),(1900,1060))` | 检测点坐标 |
| `scale_points` | `bool` | `True` | 是否按视频分辨率等比缩放检测点 |
| `reference_resolution` | `(int,int)` | `(1920,1080)` | 检测点参考分辨率 |
| `search_window` | `float` | `40.0` | 检测窗口（前 N 秒） |
| `initial_start` | `float` | `35.0` | 反向跳帧起始时间（秒） |
| `coarse_step` | `float` | `5.0` | 初始大步长（秒/次） |
| `fine_step` | `float` | `1.0` | 细化步长（秒/次） |
| `extractor` | `str` | `"ffmpeg"` | 帧提取器：`"ffmpeg"` / `"opencv"` |
| `ffmpeg_path` / `ffprobe_path` | `str` | `"ffmpeg"` / `"ffprobe"` | 可执行文件路径 |
| `log_level` | `str` | `"INFO"` | 日志级别 |
| `progress_callback` | `callable` | `None` | 全局进度回调（可被 `detect()` 参数覆盖） |

### 7.2 `VideoColorDetector`（`detector/core/detector.py`）

```python
VideoColorDetector(config: DetectorConfig = None, **overrides)
```

- `config` 与关键字覆盖不能同时使用；可用 `VideoColorDetector(confidence_threshold=0.95, color_tolerance=15.0)` 局部覆盖。
- `detect(video_path: str, progress_callback=None, detail: bool = False) -> Detection`
  - 默认只返回核心字段 `Detection`，可直接解构 `frame, time = detector.detect(...)`。
  - `detail=True` 时额外附带 `Detection.details`（`DetectionDetails`）诊断信息。
  - 进度回调签名：`callback(stage: str, progress: float, message: str)`，`progress ∈ [0,1]`。
- `close()`：释放提取器资源（建议在复用检测器后调用）。

### 7.3 `Detection`（默认结果，极简）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `detected` | `bool` | 是否检测到目标颜色 |
| `frame` | `int \| None` | 精确目标帧序号 |
| `timestamp` | `float \| None` | 目标帧时间戳（秒） |
| `details` | `DetectionDetails \| None` | 详细诊断（仅 `detail=True` 时非空） |

- 支持解构赋值：`frame, time = result`；`bool(result)` 等价于 `result.detected`。
- 兼容别名：`result.frame_index`、`result.timestamp_seconds`、`result.t`。

### 7.3.1 `DetectionDetails`（`detail=True` 时提供）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `confidence` | `float \| None` | 目标帧整体置信度（四点评分最小值） |
| `point_matches` | `list[PointMatch] \| None` | 各检测点明细（x,y,RGB,conf,matched） |
| `coarse_bracket` / `fine_bracket` | `(float,float) \| None` | 粗 / 细化定位区间 |
| `stage_stats` | `dict` | 各阶段探测帧数（coarse/fine/precise） |
| `duration_seconds` | `float` | 视频时长 |
| `resolution` | `(int,int) \| None` | 视频分辨率 |
| `message` | `str` | 人类可读的结论信息 |

### 7.4 异常体系（`detector/core/errors.py`）

| 异常 | 触发场景 |
| --- | --- |
| `DetectorError` | 所有业务异常的统一父类（捕获它即可统一处理） |
| `VideoNotFoundError` | 视频文件不存在 |
| `UnsupportedFormatError` | 非视频文件、无视频流、无法解析 |
| `FFmpegError` | ffmpeg/ffprobe 调用失败 |
| `VideoDecodeError` | 帧解码失败 |
| `ConfigError` | 配置参数非法（继承 `ValueError`） |

### 7.5 颜色工具函数（`detector/utils/color.py`）

- `hex_to_rgb("#F7F10F") -> (247,241,15)`、`rgb_to_hex((247,241,15)) -> "#F7F10F"`
- `color_confidence(pixel_rgb, target_rgb, tolerance=0.0) -> float`
- `color_match(confidence, threshold) -> bool`、`pixel_is_match(pixel, target, threshold, tolerance) -> bool`

---

## 8. 坐标适配

- 默认检测点针对 **1920×1080** 设计。
- 当视频分辨率不同且 `scale_points=True` 时，按 `x' = round(x·W/1920)`、`y' = round(y·H/1080)` 等比缩放并做越界钳制。
- 示例：320×240 视频下，默认四点缩放为 `(3,4) (317,4) (3,236) (317,236)`。
- 需要精确定位时可将 `scale_points=False` 并自定义 `points`。

---

## 9. 性能与可靠性

- **不整片解码**：每帧通过 `ffmpeg -ss <t> -i <f> -frames:v 1 -f rawvideo -pix_fmt bgr24 -` 定位抽取，仅解码目标附近帧。
- 实测（本机，1920×1080 H.264 视频）：整体检测约 **2.8 秒**，共探测 44 帧（粗 9 + 细 5 + 精确 30），远小于整片 10654 帧。
- 单帧抽取约 0.1~0.3s；逐帧阶段一次性抽取 1s 区间（约 30 帧）后内存解析，不反复起进程。
- 进度回调覆盖全部阶段；异常/越界读取安全处理。

---

## 10. 测试报告

共 **85 个测试用例，全部通过**（`py -m pytest`）。

| 文件 | 用例数 | 覆盖内容 |
| --- | --- | --- |
| `tests/test_color.py` | 14 | 颜色转换、置信度度量、容差、阈值边界 |
| `tests/test_config.py` | 19 | 默认值、参数校验（合法/非法）、覆盖、冲突 |
| `tests/test_algorithm.py` | 20 | 核心算法：命中/未命中、精确起始帧、边界 onset；结尾帧（offset）、边界 offset、进度回调、参数校验 |
| `tests/test_detector.py` | 19 | 合成视频端到端精确检测、真实视频检测、坐标缩放、异常处理、公共 API |
| `tests/test_ffmpeg.py` | 10 | ffprobe 元数据、单帧/区间抽取、合成视频无损抽取、两种提取器 |
| `tests/test_performance.py` | 3 | 单帧抽取速度、整体耗时、探测帧数上界（验证不整片解码） |

真实视频（`input1/2/3.mp4`）检测结果一致：结尾帧分别为 **frame=734/767/747（t≈24.47/25.57/24.90s）**，
位于调研确定的 [24,26]s 区间内，四点置信度均 ≥0.97。

> 注：真实视频用例在缺少 `input*.mp4` 时会自动跳过（`REQUIRE_REAL` 标记）；
> 合成视频用例始终运行，保证 CI 下核心功能可验证。

---

## 11. 常见问题

- **检测不到目标颜色？** 先确认目标颜色与视频实际颜色差异：有损编码会使颜色发生偏移，
  可适当调大 `color_tolerance` 或调低 `confidence_threshold`。
- **视频分辨率不是 1920×1080？** 保持 `scale_points=True` 即可自动缩放；如需自定义检测点，
  设置 `scale_points=False` 并传入 `points`。
- **FFmpeg 未加入 PATH？** 通过 `ffmpeg_path` / `ffprobe_path` 配置可执行文件完整路径。
- **VFR（可变帧率）视频？** 本模块按平均帧率近似换算帧序号，CFR 视频为精确结果。
