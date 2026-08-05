# ADR-0023：Streamlit Dashboard 的有数据冒烟测试

- **状态**：Superseded（Streamlit 已退役，相关 AppTest 随 entry 一起删除）
- **承接方案**：docs/plans/0001-production-readonly-rag-component.md C0
- **日期**：2026-08-02
- **决策范围**：如何自动验证六个 Dashboard 页面在真实本地数据上可渲染
- **说明**：本文记录实现知识和测试边界，不修改 `DEV_SPEC.md`

## 问题

页面级单元测试通常注入 Fake Service，适合验证表格、指标和按钮行为，但不能证明完整运行时
配置能正确装配 Chroma、SQLite、BM25、Trace 和 Evaluation Service。只启动 Streamlit 进程
并检查端口也不够，因为某个非首页页面仍可能在执行时抛出 Python 异常。

因此 I2 增加一条“有数据冒烟测试”：先准备真实本地状态，再逐页运行 Streamlit 脚本，确认
所有页面都完成渲染且 `app.exception` 为空。

## 测试结构

```text
临时 PDF
   │ ingest_main
   ├─ SQLite generation / document metadata
   ├─ Chroma vectors
   ├─ BM25 snapshot
   └─ Ingestion Trace

真实 Query
   └─ Query Trace

同一份 RAG_SETTINGS_PATH
   ├─ 系统总览
   ├─ 数据浏览器
   ├─ Ingestion 管理
   ├─ Ingestion 追踪
   ├─ Query 追踪
   └─ 评估面板
```

测试不是手写几条“长得像真实数据”的 SQLite 记录，而是调用生产摄取与查询入口。这样能同时
验证存储 Schema、active generation 可见性和 Dashboard Service 的装配契约。所有路径都
位于 pytest 的临时目录，并通过 `RAG_SETTINGS_PATH` 注入，不读取或污染开发者的默认索引。

## 为什么每个页面独立执行 render

当前应用使用 `st.navigation` 注册 Python callable 页面，而不是 `pages/*.py` 文件式多页
应用。Streamlit `AppTest.switch_page()` 接受的是相对脚本文件路径，不能可靠地切换这种
callable 页面。

测试因此通过 `AppTest.from_string()` 分别导入六个页面的 `render()`：

```python
script = """\
from src.observability.dashboard.pages.query_traces import render
render()
"""
app = AppTest.from_string(script).run()
assert not app.exception
```

这仍然使用页面的默认 composition root，所以页面会从环境变量加载真实配置并创建真实
Service；它只绕过侧边栏导航动作，不绕过页面逻辑。默认首页和导航注册本身已经由
`tests/integration/test_dashboard_app.py` 覆盖，两类测试组合后职责更清楚。

## 为什么使用确定性 Embedding

冒烟测试关心页面能否消费真实索引，不评估外部 Embedding 服务。因此测试注册一个进程内
确定性 Provider，让摄取和查询在无网络、无 API Key 的环境下可复现。Dashboard 的 AppTest
脚本与 pytest 运行在同一 Python 进程，所以可以使用这个注册项；测试结束后必须注销，避免
全局 Factory 注册表污染其他用例。

这和 MCP 子进程 E2E 不同：MCP Server 是新进程，不能继承测试 Provider，因此 I1 在查询
子进程中关闭 Dense，只使用已持久化的 BM25。

## 断言层次

冒烟测试只断言两件事：

1. 每个页面产生预期标题，证明执行的是目标页面；
2. `app.exception` 为空，证明 Streamlit 脚本没有未处理的 Python 异常。

具体指标值、表格形状、按钮反馈和 Trace 排名变化继续由较快的集成测试负责。避免在 E2E
重复所有展示断言，可以减少 UI 文案调整带来的脆弱失败，同时保留真实运行时装配的保障。

## 能发现与不能发现的问题

| 能发现 | 不能发现 |
|--------|----------|
| 配置字段缺失或路径装配错误 | CSS 像素级错位 |
| Chroma、SQLite、BM25 Schema 不兼容 | 真实浏览器中的网络与字体问题 |
| 页面读取真实数据时出现类型或渲染异常 | 用户连续点击、上传和删除的完整交互行为 |
| Trace JSONL 与页面解析契约漂移 | 大数据量下的性能和内存上限 |
| Evaluation Service 初始化失败 | 外部模型 API 的可用性 |

`AppTest` 是 Python 层的 Streamlit 测试工具，不是浏览器自动化替代品。若未来需要验证布局、
下载、文件上传或跨页面交互，应补充 Playwright 等浏览器级测试，而不是让当前冒烟用例承担
所有 UI 验收职责。

## 关联实现

- `tests/e2e/test_dashboard_smoke.py`
- `src/observability/dashboard/app.py`
- `src/observability/dashboard/pages/`
- `src/observability/dashboard/services/`
- `tests/integration/test_dashboard_app.py`
