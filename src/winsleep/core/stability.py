"""让电源计划的决定别抖。

**为什么要有这一层。** 电源管理的判断是「CPU 低于阈值、并且空闲够久 →
用省电计划」。阈值是一次比较：26% 算忙，24% 算闲。机器上��常有后台任务、
杀毒、代理在跑，读数就在阈值附近晃。一台其实很安静的机器实测就在 25%
上下摆，后台服务于是每五秒切一次计划——写一次电源计划、读回来、写一次，
一天下来几千次。界面上「正在编辑的计划」也跟着换，用户刚改的数���当场
就没了。

两个办法治它：

* **读数先平均。** ``CpuAverage`` 留着最近几次采样，用平均值判断。
* **决定要稳。** ``PlanStabilizer`` 要求新的计划连续一段时间都成立，
  才真的切过去。抖动的读数凑不满这个时间，计划就一直不动。

两个都是纯逻辑，不碰 Windows API，可以离线测。
"""

from __future__ import annotations

__all__ = ["CpuAverage", "PlanStabilizer"]


class CpuAverage:
    """最近几次 CPU 读数的平均值。

    **为什么不是直接用最后一次读数。** ``psutil.cpu_percent`` 给的是
    「上一次调用到现在」这一小段的平均值，一段一段的，边界上必然抖。
    阈值是单点比较，抖一下就是「忙 / 不忙」来回换。

    窗口取 4 个采样（每 5 秒一拍，约 20 秒）：足够把一次抖动抹平，又不会
    让「游戏真的跑起来了」这种明显的变化拖太久才反应过来。
    """

    def __init__(self, window: int = 4) -> None:
        self._window = max(1, int(window))
        self._samples: list[float] = []

    @property
    def window(self) -> int:
        return self._window

    def add(self, value: float) -> float:
        """收一次读数，返回平均之后的 CPU 占用。"""
        try:
            sample = float(value)
        except (TypeError, ValueError):
            sample = 0.0
        self._samples.append(max(0.0, sample))
        if len(self._samples) > self._window:
            self._samples.pop(0)
        return self.value()

    def value(self) -> float:
        """当前的平均值。还没采到样时是 0。"""
        if not self._samples:
            return 0.0
        return sum(self._samples) / len(self._samples)

    def reset(self) -> None:
        self._samples.clear()


class PlanStabilizer:
    """新的计划要稳定一段时间才真的切过去。

    **没有这一层会怎样。** 决定每五秒在「忙 / 闲」之间来回，后台服务就
    每五秒写一次电源计划。实测一台安静机器上就是这样，一天几千次。

    **有了这一层。** 想要换计划时先记下「想换成什么」和「从什么时候开始
    想的」；只有这个念头连续保持够久（默认 15 秒）才真的换。抖动的决定
    每次都在重置这个计时器，于是计划一次都不动。

    刚启动时没有当前计划，就立刻用想要的——不用等，这是第一次决定。
    """

    def __init__(self, hold_seconds: float = 15.0) -> None:
        self._hold = max(0.0, float(hold_seconds))
        self._current: str | None = None
        self._pending: str | None = None
        self._pending_since: float = 0.0

    @property
    def hold_seconds(self) -> float:
        return self._hold

    @property
    def current(self) -> str | None:
        """现在生效的计划。"""
        return self._current

    @property
    def pending(self) -> str | None:
        """正在等稳定的那个计划。"""
        return self._pending

    def update(self, now: float, wanted: str | None) -> str | None:
        """喂进这次想要的计划，返回「现在应该用哪个计划」。

        - ``wanted`` 是空：这一步不作数，交给别处（=「别动计划」）。
        - 现在还没有计划：立刻用 ``wanted``（刚启动不用等）。
        - ``wanted`` 就是现在的：清掉待定，保持现在的。
        - ``wanted`` 变了但还没稳够久：保持现在的。
        - 稳够了：换过去。
        """
        if not wanted:
            # 决定是「别动计划」（比如还没配计划）。这时**不能**把上一个
            # 计划再写回去——那会变成跟别的工具抢电源计划。
            self._pending = None
            return None
        if self._current is None:
            self._current = wanted
            self._pending = None
            return self._current
        if wanted == self._current:
            self._pending = None
            return self._current
        if wanted != self._pending:
            self._pending = wanted
            self._pending_since = now
            return self._current
        if now - self._pending_since >= self._hold:
            self._current = wanted
            self._pending = None
        return self._current
