"""
LLM 调用熔断器 — 三态状态机 + 请求超时保护。

面试知识点：
- 熔断器 vs 重试的区别？
  重试假设故障是暂时的，立即重试（可能加重下游负担）。
  熔断假设故障会持续，快速失败 + 定期探测（保护下游也保护自己）。

- 为什么熔断器包裹 tenacity 而不是替代它？
  两者解决不同问题：tenacity 处理偶发网络抖动（重试 3 次 2s/4s/8s），
  熔断器处理持续性故障（DeepSeek API 挂了）。tenacity 的 RetryError
  （3 次全失败）才算熔断器的 1 次失败——避免单次网络抖动触发熔断。

- 三态状态机原理：
  CLOSED（正常）→ 连续失败 N 次 → OPEN（快速失败）
  OPEN → 等待 recovery_timeout → HALF_OPEN（允许少量探测）
  HALF_OPEN → 探测成功 → CLOSED（恢复）/ 探测失败 → OPEN（重置计时器）
"""
import time
import asyncio
from enum import Enum
from app.logger import get_logger

cb_log = get_logger("circuit_breaker")


class CircuitState(Enum):
    """熔断器三态"""
    CLOSED = "closed"        # 正常：请求通过，累计失败计数
    OPEN = "open"            # 熔断：快速失败，不调用下游
    HALF_OPEN = "half_open"  # 半开：允许少量探测请求


class CircuitBreaker:
    """LLM 调用熔断器 —— 保护 DeepSeek API，避免雪崩效应。

    使用方式：
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30)

        # 在调用 LLM 之前：
        if not cb.allow_request():
            return fallback_response

        try:
            result = await llm_call()
            cb.record_success()
        except Exception:
            cb.record_failure()
            raise
    """

    def __init__(
        self,
        name: str = "llm",
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 1,
    ):
        """
        Args:
            name: 熔断器名称（用于日志标识）
            failure_threshold: 连续失败 N 次后触发熔断（CLOSED → OPEN）
            recovery_timeout: 熔断后等待多少秒进入 HALF_OPEN
            half_open_max_calls: HALF_OPEN 状态下允许的最大探测请求数
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._half_open_calls = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """当前状态（只读）"""
        return self._state

    async def allow_request(self) -> bool:
        """检查是否允许请求通过。

        在 CLOSED 或 HALF_OPEN（未超配额）时返回 True。
        在 OPEN 时检查是否已过恢复期，是则转入 HALF_OPEN，否则返回 False。
        """
        async with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    cb_log.info(
                        "熔断器 [%s]: OPEN → HALF_OPEN（%.1fs 恢复期已到，开始探测）",
                        self.name, elapsed,
                    )
                    return True
                else:
                    cb_log.warning(
                        "熔断器 [%s]: OPEN 拒绝请求（%.1fs/%.1fs 剩余）",
                        self.name, elapsed, self.recovery_timeout,
                    )
                    return False

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    cb_log.warning(
                        "熔断器 [%s]: HALF_OPEN 探测配额已用完（%d/%d），拒绝请求",
                        self.name, self._half_open_calls, self.half_open_max_calls,
                    )
                    return False
                self._half_open_calls += 1
                cb_log.info(
                    "熔断器 [%s]: HALF_OPEN 允许探测请求（%d/%d）",
                    self.name, self._half_open_calls, self.half_open_max_calls,
                )
                return True

            return True  # 防御性 fallback

    async def record_success(self):
        """记录一次成功调用。CLOSED → 重置失败计数；HALF_OPEN → 恢复至 CLOSED。"""
        async with self._lock:
            prev_state = self._state
            self._failure_count = 0
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                cb_log.info("熔断器 [%s]: HALF_OPEN → CLOSED（探测成功，已恢复）", self.name)
            # CLOSED 状态下成功只需重置计数器（已做），无需日志

    async def record_failure(self):
        """记录一次失败调用。CLOSED → 累计，达阈值 → OPEN；HALF_OPEN → 立即回到 OPEN。"""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    cb_log.warning(
                        "熔断器 [%s]: CLOSED → OPEN（连续失败 %d/%d 次，熔断 %ds）",
                        self.name, self._failure_count, self.failure_threshold,
                        int(self.recovery_timeout),
                    )
                else:
                    cb_log.warning(
                        "熔断器 [%s]: 失败累计 %d/%d",
                        self.name, self._failure_count, self.failure_threshold,
                    )

            elif self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                cb_log.warning(
                    "熔断器 [%s]: HALF_OPEN → OPEN（探测失败，重新熔断 %ds）",
                    self.name, int(self.recovery_timeout),
                )

    def reset(self):
        """手动重置熔断器到 CLOSED 状态（管理接口用）"""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._half_open_calls = 0
        cb_log.info("熔断器 [%s]: 手动重置 → CLOSED", self.name)


# ==================== 全局单例 ====================

_circuit_breaker: CircuitBreaker | None = None


def get_circuit_breaker() -> CircuitBreaker:
    """获取或创建熔断器单例。首次调用时从 settings 读取配置。"""
    global _circuit_breaker
    if _circuit_breaker is None:
        from config.settings import settings
        _circuit_breaker = CircuitBreaker(
            name="llm",
            failure_threshold=settings.CB_FAILURE_THRESHOLD,
            recovery_timeout=settings.CB_RECOVERY_TIMEOUT,
            half_open_max_calls=settings.CB_HALF_OPEN_MAX,
        )
        cb_log.info(
            "熔断器初始化: threshold=%d recovery=%.1fs half_open_max=%d",
            settings.CB_FAILURE_THRESHOLD,
            settings.CB_RECOVERY_TIMEOUT,
            settings.CB_HALF_OPEN_MAX,
        )
    return _circuit_breaker
