# -*- coding: utf-8 -*-
"""Web UI /api/trim 终点参数（区间裁剪）单元测试。

覆盖设计文档 §5.4 的 webui 测试项：
- /api/trim 终点参数校验（方式冲突 / end ≤ start / 超范围 → 400）
- 终点留空等价性（end 缺省 = 原有保留到片尾行为）
- 批量 Trim 不使用终点（显式忽略 end_* 参数）
"""

import os
import sys

import pytest

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # src/webui
_SRC = os.path.dirname(_BASE)  # src
for _p in (_SRC, _BASE, os.path.join(_BASE, "_vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from webui import app as webui_app  # noqa: E402
from trimmer.core.probe import AudioStream, MediaInfo, VideoStream  # noqa: E402


def _media(duration=100.0, fps=30.0, nb_frames=3000):
    video = VideoStream(codec="h264", fps=fps,
                        bit_rate=1000, pix_fmt="yuv420p")
    audio = AudioStream(codec="aac", sample_rate=48000, channels=2, bit_rate=128)
    return MediaInfo(path="x", duration=duration,
                     video=video, audio=audio, nb_frames=nb_frames)


@pytest.fixture()
def client(monkeypatch):
    # 阻止真实创建后台任务与真实媒体探测
    monkeypatch.setattr(webui_app.manager, "create", lambda *a, **k: "job-test")
    monkeypatch.setattr(webui_app, "trimmer_probe", lambda p: _media())
    webui_app.app.config["TESTING"] = True
    with webui_app.app.test_client() as c:
        yield c


def _video(tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"x")
    return str(video)


class TestParseEndPoint:
    """_parse_end_point 矩阵测试。"""

    def test_no_end_variants(self):
        # null / 缺省 / "none" + 空值 → 均为无终点
        assert webui_app._parse_end_point({}) == (None, None)
        assert webui_app._parse_end_point({"end_mode": None, "end_value": None}) == (None, None)
        assert webui_app._parse_end_point({"end_mode": "none", "end_value": ""}) == (None, None)

    def test_frame(self):
        assert webui_app._parse_end_point(
            {"end_mode": "frame", "end_value": "108000"}) == (108000, None)

    def test_timestamp(self):
        assert webui_app._parse_end_point(
            {"end_mode": "timestamp", "end_value": "3600.5"}) == (None, 3600.5)

    def test_mode_without_value(self):
        with pytest.raises(ValueError):
            webui_app._parse_end_point({"end_mode": "frame", "end_value": ""})

    def test_value_without_mode(self):
        with pytest.raises(ValueError):
            webui_app._parse_end_point({"end_mode": "none", "end_value": "100"})

    def test_bad_number(self):
        with pytest.raises(ValueError):
            webui_app._parse_end_point({"end_mode": "timestamp", "end_value": "abc"})

    def test_bad_mode(self):
        with pytest.raises(ValueError):
            webui_app._parse_end_point({"end_mode": "bogus", "end_value": "1"})


class TestBuildTrimmerConfig:
    """build_trimmer_config 终点参数传递。"""

    def _params(self, **kw):
        base = {"start_mode": "frame", "start_value": "735",
                "suffix": "_trim", "output_mode": "full"}
        base.update(kw)
        return base

    def test_no_end_equivalence(self):
        # 终点留空 = 现状行为：end 字段均为 None
        cfg = webui_app.build_trimmer_config("x.mp4", self._params(), "out")
        assert cfg.end_timestamp is None and cfg.end_frame is None
        assert cfg.frame == 735 and cfg.timestamp is None

    def test_end_timestamp(self):
        cfg = webui_app.build_trimmer_config(
            "x.mp4", self._params(end_mode="timestamp", end_value="3600"), "out")
        assert cfg.end_timestamp == 3600.0 and cfg.end_frame is None

    def test_end_frame(self):
        cfg = webui_app.build_trimmer_config(
            "x.mp4", self._params(end_mode="frame", end_value="108000"), "out")
        assert cfg.end_frame == 108000 and cfg.end_timestamp is None

    def test_end_independent_of_start_mode(self):
        # 起点=时间戳 × 终点=帧序号自由组合
        cfg = webui_app.build_trimmer_config(
            "x.mp4", self._params(start_mode="timestamp", start_value="24.9",
                                  end_mode="frame", end_value="108000"), "out")
        assert cfg.timestamp == 24.9 and cfg.frame is None
        assert cfg.end_frame == 108000 and cfg.end_timestamp is None


class TestApiTrimValidation:
    """/api/trim 终点参数同步校验（400 路径）。"""

    def _post(self, client, path, params):
        return client.post("/api/trim", json={
            "path": path, "output_dir": "out", "params": params})

    def test_end_before_start_400(self, client, tmp_path):
        r = self._post(client, _video(tmp_path), {
            "start_mode": "timestamp", "start_value": "50",
            "end_mode": "timestamp", "end_value": "50"})
        assert r.status_code == 400
        assert "终点" in r.get_json()["message"]
        # end < start 同样报错
        r = self._post(client, _video(tmp_path), {
            "start_mode": "timestamp", "start_value": "50",
            "end_mode": "timestamp", "end_value": "10"})
        assert r.status_code == 400

    def test_end_exceeds_range_400(self, client, tmp_path):
        r = self._post(client, _video(tmp_path), {
            "start_mode": "timestamp", "start_value": "10",
            "end_mode": "timestamp", "end_value": "999"})
        assert r.status_code == 400
        # 帧序号超范围
        r = self._post(client, _video(tmp_path), {
            "start_mode": "frame", "start_value": "30",
            "end_mode": "frame", "end_value": "100000"})
        assert r.status_code == 400

    def test_mode_value_mismatch_400(self, client, tmp_path):
        # 方式为「无」但填了终点值
        r = self._post(client, _video(tmp_path), {
            "start_mode": "timestamp", "start_value": "10",
            "end_mode": "none", "end_value": "88"})
        assert r.status_code == 400
        # 选了方式但未填值
        r = self._post(client, _video(tmp_path), {
            "start_mode": "timestamp", "start_value": "10",
            "end_mode": "frame", "end_value": ""})
        assert r.status_code == 400

    def test_valid_end_accepted_200(self, client, tmp_path):
        r = self._post(client, _video(tmp_path), {
            "start_mode": "timestamp", "start_value": "10",
            "end_mode": "timestamp", "end_value": "60"})
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_no_end_equivalence_200(self, client, tmp_path):
        # 终点留空：请求行为与原有版本一致（不返回 400，正常创建任务）
        r = self._post(client, _video(tmp_path), {
            "start_mode": "frame", "start_value": "735"})
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_mixed_modes_order_200(self, client, tmp_path):
        # 起点=帧序号（735 → 24.467s）、终点=时间戳 60s：换算后 end > start
        r = self._post(client, _video(tmp_path), {
            "start_mode": "frame", "start_value": "735",
            "end_mode": "timestamp", "end_value": "60"})
        assert r.status_code == 200

    def test_start_errors_deferred_to_job(self, client, tmp_path):
        # 起点参数错误不在此处拦截：维持现状交由后台任务通过 SSE 报错
        r = self._post(client, _video(tmp_path), {
            "start_mode": "frame", "start_value": "",
            "end_mode": "timestamp", "end_value": "60"})
        assert r.status_code == 200


class TestBatchTrimIgnoresEnd:
    """批量 Trim 维持现状：以检测结果为起点、无终点（§2.2 设计边界）。"""

    def test_batch_strips_end_params(self, monkeypatch, tmp_path):
        calls = []

        class _Result:
            output_files = [str(tmp_path / "o.mp4")]
            message = "流复制（未重编码）"

        def fake_build(path, params, output_dir, start_override=None):
            calls.append(params)
            return object()

        def fake_run(self, progress_cb=None, confirm_cb=None, cancel_event=None):
            return _Result()

        monkeypatch.setattr(webui_app, "build_trimmer_config", fake_build)
        monkeypatch.setattr(webui_app.Trimmer, "run", fake_run)

        class _Event:
            @staticmethod
            def is_set():
                return False

        job = type("Job", (), {"publish": lambda self, *a, **k: None,
                               "cancel_event": _Event(),
                               "file_cancel": set()})()
        result = webui_app.run_batch_trim(job, {
            "files": [{"path": _video(tmp_path), "frame": 100}],
            "params": {"start_mode": "frame", "start_value": "50",
                       "end_mode": "timestamp", "end_value": "60",
                       "suffix": "_trim"},
            "output_dir": str(tmp_path),
        })
        assert calls, "build_trimmer_config 应被调用"
        assert "end_mode" not in calls[0] and "end_value" not in calls[0]
        assert result["summary"]["ok"] == 1


class TestJobEventReplay:
    """任务事件回放缓冲：轮询端带游标增量拉取，与 SSE 同粒度。"""

    @staticmethod
    def _job():
        from webui.jobs import Job
        return Job("job-replay", "detect", "颜色检测")

    def test_publish_keeps_seq_and_latest_progress(self):
        job = self._job()
        job.publish("started", {})
        job.publish("progress", {"stage": "coarse", "progress": 0.1, "message": "a"})
        job.publish("progress", {"stage": "coarse", "progress": 0.2, "message": "b"})
        job.publish("progress", {"stage": "fine", "progress": 0.6, "message": "c"})
        assert [i["seq"] for i in job.recent] == [1, 2, 3, 4]
        assert job.last_progress == {"stage": "fine", "progress": 0.6, "message": "c"}

    def test_events_since_returns_delta(self):
        job = self._job()
        for i in range(5):
            job.publish("progress", {"progress": i / 10})
        assert [i["seq"] for i in job.events_since(0)] == [1, 2, 3, 4, 5]
        assert [i["seq"] for i in job.events_since(3)] == [4, 5]
        assert job.events_since(99) == []

    def test_recent_buffer_bounded(self):
        job = self._job()
        for i in range(2500):  # 超过 maxlen=2000：最早的被挤出
            job.publish("progress", {"progress": i / 2500})
            job.drain_queue()  # 及时排空，避免 SSE 队列打满导致 put 阻塞等待
        assert len(job.recent) == 2000
        assert job.recent[0]["seq"] == 501
        assert job._ev_seq == 2500
        # 游标早于被挤出的区间：只返回现存部分（前端按 seq 前进，不受影响）
        assert job.events_since(0)[0]["seq"] == 501

    def test_job_status_replay_api(self, client, monkeypatch):
        job = self._job()
        job.publish("progress", {"stage": "coarse", "progress": 0.1, "message": "a"})
        job.publish("progress", {"stage": "fine", "progress": 0.5, "message": "b"})
        monkeypatch.setitem(webui_app.manager._jobs, "job-replay", job)
        d = client.get("/api/jobs/job-replay?since=1").get_json()
        assert d["ok"] and d["status"] == "running"
        assert [e["seq"] for e in d["events"]] == [2]
        assert d["events"][0]["data"]["stage"] == "fine"
        # 不带 since 不附事件（SSE 断线兜底等旧调用方不受影响）
        assert "events" not in client.get("/api/jobs/job-replay").get_json()
