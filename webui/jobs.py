# -*- coding: utf-8 -*-
"""异步任务管理器：后台线程 + SSE 事件流 + 取消机制。

用于在 Web 后端以非阻塞方式执行 detector / trimmer 等耗时模块，
通过 Server-Sent Events 向浏览器推送进度与最终结果，支持取消。
"""
from __future__ import annotations

import itertools
import json
import queue
import threading
import time
from collections import deque
from typing import Any, Callable, Dict, Optional


class JobCancelled(BaseException):
    """任务取消信号。

    继承自 :class:`BaseException` 而非 :class:`Exception`，
    以绕过 detector 算法中 ``except Exception`` 对进度回调异常的吞没，
    确保取消能够沿调用栈向上传播。
    """


class Job:
    """单个异步任务的运行状态与事件队列。"""

    def __init__(self, job_id: str, kind: str, name: str):
        self.id = job_id
        self.kind = kind
        self.name = name
        self.status = "running"  # running / done / error / cancelled
        self.cancel_event = threading.Event()
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        # 事件历史（供状态查询）与实时队列（供 SSE 消费）
        self._events: deque = deque(maxlen=1000)
        self._queue: "queue.Queue" = queue.Queue(maxsize=2000)

    def publish(self, event: str, data: Any) -> None:
        """追加一条事件；消费方通过 SSE 或状态接口获取。"""
        payload = {"type": event, "data": data, "ts": time.time()}
        self._events.append(payload)
        try:
            self._queue.put(payload, timeout=1)
        except queue.Full:
            pass

    def drain_queue(self):
        """非阻塞排空实时队列（供 SSE 连接时先消费既有事件）。"""
        items = []
        while True:
            try:
                items.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return items


class JobManager:
    """全局任务注册表；每个任务在独立守护线程中执行。"""

    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._counter = itertools.count(1)

    def create(self, kind: str, name: str, func: Callable[[Job], Any]) -> str:
        """创建并启动任务，返回 job_id。"""
        with self._lock:
            jid = f"{kind}-{next(self._counter)}"
            job = Job(jid, kind, name)
            self._jobs[jid] = job
        job.publish("started", {"kind": kind, "name": name})
        thread = threading.Thread(
            target=self._run, args=(job, func), daemon=True, name=f"job-{jid}"
        )
        thread.start()
        return jid

    def get(self, jid: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(jid)

    def cancel(self, jid: str) -> bool:
        """置位取消事件；模块在回调或执行循环中响应并终止。"""
        job = self.get(jid)
        if job is None:
            return False
        job.cancel_event.set()
        return True

    def _run(self, job: Job, func: Callable[[Job], Any]) -> None:
        try:
            result = func(job)
            if job.cancel_event.is_set():
                job.status = "cancelled"
                job.publish("cancelled", {"message": "任务已取消"})
            else:
                job.status = "done"
                job.result = result or {}
                job.publish("done", result or {})
        except JobCancelled:
            job.status = "cancelled"
            job.publish("cancelled", {"message": "任务已取消"})
        except KeyboardInterrupt:
            job.status = "cancelled"
            job.publish("cancelled", {"message": "任务已取消"})
        except Exception as exc:  # noqa: BLE001 - 统一兜底并返回友好信息
            job.status = "error"
            job.error = str(exc)
            job.publish("error", {"message": str(exc)})


#: 全局唯一实例
manager = JobManager()


def sse_payload(item: dict) -> str:
    """将事件字典格式化为 SSE 报文。"""
    return (
        f"event: {item['type']}\n"
        f"data: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
    )
