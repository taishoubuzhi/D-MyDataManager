"""下载队列的共享入口：懒建、跟着配置热改、退出时收干净。

`app.core.download.service` 是程序本体与插件共用的那一个队列，这里盯的是插件最容易踩到的
几条语义——「没人用就别把队列建起来」「改配置要立刻生效」「退出之后还能重新来一份」。
不碰网络：整个过程不排任何下载任务。
"""

from __future__ import annotations

import unittest

from app.core.config import config
from app.core.download import service
from tests.harness import IsolatedCase


class ServiceCase(IsolatedCase):

    def tearDown(self) -> None:
        service.shutdown(wait=1.0)
        super().tearDown()

    def test_lazy_until_first_use(self) -> None:
        """打开页面看一眼不该建队列：只有真要用了才建。"""
        self.assertIsNone(service.current_manager())
        self.assertFalse(service.is_running())

        manager = service.manager()
        self.assertIs(service.current_manager(), manager)
        self.assertTrue(service.is_running())
        self.assertIs(service.manager(), manager, "第二次取应当还是同一份队列")

    def test_options_follow_config(self) -> None:
        config.set(config.downloadConcurrent, 5)
        config.set(config.downloadProxy, " http://127.0.0.1:8080 ")
        config.set(config.downloadRetries, 4)

        options = service.options()
        self.assertEqual(options.concurrent, 5)
        self.assertEqual(options.limit, 5)
        self.assertEqual(options.proxy, "http://127.0.0.1:8080", "代理地址两头的空白要去掉")
        self.assertEqual(options.retries, 4)

    def test_configure_applies_to_live_queue(self) -> None:
        manager = service.manager()
        config.set(config.downloadConcurrent, 5)
        service.configure()
        self.assertEqual(manager.limit, 5)

        config.set(config.downloadSequential, True)
        service.configure()
        self.assertEqual(manager.limit, 1, "切成顺序下载后有效并发应当是 1")
        self.assertEqual(manager.options.concurrent, 5, "顺序下载不该把用户设的并行数抹掉")

        config.set(config.downloadSequential, False)
        service.configure()
        self.assertEqual(manager.limit, 5)

    def test_configure_does_not_build_queue(self) -> None:
        """页面刷新会顺手同步设置：这条路径不能变成「看一眼就建队列」。"""
        service.configure()
        self.assertFalse(service.is_running())

    def test_shutdown_clears_and_rebuilds(self) -> None:
        first = service.manager()
        service.shutdown(wait=1.0)
        self.assertFalse(service.is_running())
        self.assertIsNone(service.current_manager())

        service.shutdown(wait=1.0)  # 再收一次也不该出事

        second = service.manager()
        self.assertIsNot(second, first, "退出后应当能重新建一份全新的队列")
        self.assertTrue(service.is_running())


if __name__ == "__main__":
    unittest.main()
