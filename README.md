# 结构化信息提取：LLM Agent 第一阶段练习

一个不依赖具体模型厂商的 Python 命令行项目，使用 OpenAI 兼容的 `/chat/completions` 接口，练习消息结构、Structured Output、Tool Calling、Streaming、Token 成本与错误处理。

## 1. 学习目标

输入：

> 客户希望下周一下午预约产品演示，参加人有张三和李四。

当参考日期为 `2026-07-13` 时，输出：

```json
{
  "event_type": "product_demo",
  "date": "2026-07-20",
  "period": "afternoon",
  "attendees": ["张三", "李四"]
}
```

“下周一”不是固定日期。程序把参考日期和时区放进系统消息；日常运行时默认取本机当天日期，测试时显式传入参考日期以保证结果可复现。

## 2. 项目结构

```text
src/event_extractor/
├── schema.py    # 输出契约与业务校验
├── prompts.py   # system/user 消息
├── client.py    # API、Structured Output、Streaming、Tool Calling、重试
├── usage.py     # Token 与成本
├── errors.py    # 错误分类
└── cli.py       # 命令行入口
tests/
└── test_extractor.py
```

## 3. 运行

需要 Python 3.11+。

```bash
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e .

export API_KEY='你的密钥'          # PowerShell: $env:API_KEY='你的密钥'
export BASE_URL='https://api.openai.com/v1'
export MODEL='你的模型名'

event-extract '客户希望下周一下午预约产品演示，参加人有张三和李四。' \
  --reference-date 2026-07-13 --timezone Asia/Shanghai
```

也可使用 `python -m event_extractor ...`。`BASE_URL` 按厂商要求填写，通常包含 `/v1`；模型必须兼容本项目使用的 JSON Schema Structured Output。程序不会自动读取 `.env`，`.env.example` 仅作配置模板。

### Streaming

```bash
event-extract '客户希望下周一下午预约产品演示，参加人有张三和李四。' \
  --reference-date 2026-07-13 --stream
```

流式 JSON 增量写到 stderr，最终校验后的 JSON 写到 stdout。这样可以把机器可读结果重定向到文件，同时保留过程输出：

```bash
event-extract '...' --stream > result.json
```

### Tool Calling

```bash
event-extract '...' --reference-date 2026-07-13 --tools
```

模型可请求 `get_calendar_context`，程序执行本地函数，把可信日期上下文以 `role=tool` 消息送回模型，再获取最终结构化结果。为便于学习，此练习中的 Tool Calling 与 Streaming 分开演示。

注意：本例不调用工具也能完成。真实项目不要为了让模型“ Agent 化”而强加工具；日期计算等确定性逻辑应尽量由代码完成。

### Token 与成本

API 返回 usage 时，程序显示输入、输出和总 Token。成本价格不写死，因为不同厂商和模型价格不同：

```bash
event-extract '...' --input-price 0.15 --output-price 0.60
```

两个价格均以“每 100 万 Token、你的计费货币”为单位。未提供 usage 时不猜测成本。

## 4. 离线测试

```bash
python -m unittest discover -s tests -v
```

测试使用模拟 API，不消耗 Token，覆盖：

- Structured Output 请求与 Pydantic 结果
- system/user 消息结构
- SSE 流式拼接与 usage
- Tool Calling 往返
- 429 重试、500 最终失败
- 非法 JSON 与非法日期的区别
- Token 成本计算

## 5. 关键设计

1. **两层校验**：`json.loads` 只说明内容是 JSON；Pydantic 继续检查字段、枚举、额外字段及真实日期。
2. **不让模型猜缺失信息**：日期未知用 `null`，时段未知用 `unknown`，参加人只保留明确提到的人名。
3. **重试边界**：对超时、网络错误、429 和部分 5xx 做有限次数指数退避；普通 4xx 不盲目重试。流式响应中途失败则终止，避免重复输出。
4. **结果与日志分流**：stdout 只放最终 JSON，诊断、流式增量、Token 和成本放 stderr。
5. **价格外置**：成本根据本次 usage 与用户配置的单价计算，不在代码里维护易过期的价格表。

## 6. 建议练习顺序

1. 修改 `EventExtraction`，增加可选的 `location` 字段，并同步补测试。
2. 输入“下周三上午十点，王五想预约演示”，观察缺少的信息是否被误补。
3. 分别模拟 401、429、500、截断和非法 JSON，比较错误处理策略。
4. 将日历工具替换为真正的日期计算函数，让模型提交“相对日期表达式”，由 Python 得出日期。
5. 增加第二次修正请求，将历史 assistant 消息加入 messages，练习多轮上下文。
6. 在日志中记录模型名、延迟、重试次数和 Token，但不要记录 API Key。
