# BUAA iClass Checkin

仅 CLI 模式的北航 iClass 自动签到工具。

本项目整合了 `duaa` 的两阶段 cron 工作流，以及 `BUAASignTool` 的 WebVPN / iClass 访问方式：

1. 每天早上查询当天课表；
2. 根据配置为当天每节未签到课程注册一次性 cron 任务；
3. 在配置指定的时间点自动通过 WebVPN 执行签到。

> 请严格遵循学校相关规章制度，合理使用本工具。不要用于恶意并发请求或破坏系统稳定性的行为。

## 特性

- 纯 CLI，无 GUI 依赖；
- 通过 `d.buaa.edu.cn` WebVPN 访问 iClass；
- 支持校外服务器运行；
- 每天自动获取当天课程；
- 支持配置是否启用自动签到；
- 支持配置签到时间：上课前 10 分钟到下课前 1 分钟；
- 签到前会再次检查课程状态，已签则跳过；
- 支持按课程 ID / 排课 ID / 课程名过滤；
- **签到重试**: 可恢复错误 (网络异常、老师未发起签到) 自动重试，最多 3 次，间隔 30 秒；
- **Session 持久化**: 登录后的 session 缓存到 `state/session.json`，6 小时内免重复登录；
- **日志写文件**: 所有日志同时输出到控制台和 `state/iclass-checkin.log`。

## 安装

```bash
git clone git@github.com:operapeking/buaa-iclass-checkin.git
cd buaa-iclass-checkin
cp config.example.json config.json
```

编辑 `config.json`：

```json
{
    "student_id": "你的学号",
    "password": "统一身份认证密码",
    "course_ids": [],
    "auto_checkin": {
        "enabled": true,
        "offset_minutes": 10
    }
}
```

说明：

- `course_ids`: 空数组表示所有课程；也可以填写课程 ID、排课 ID 或课程名。
- `auto_checkin.enabled`: 是否启用自动签到。设为 `false` 时，每天只查询并缓存课表，不注册课程签到任务。
- `auto_checkin.offset_minutes`: 以课程开始时间为基准的签到偏移分钟数：
  - `-10` 表示上课前 10 分钟；
  - `0` 表示上课铃响时；
  - `10` 表示上课后 10 分钟；
  - 允许范围：上课前 10 分钟到下课前 1 分钟。

运行安装脚本：

```bash
bash install.sh
```

安装脚本会：

- 检查或安装 Python 依赖：`requests`、`beautifulsoup4`；
- 设置每日 07:00 查询课表的 cron 任务。

## 使用

手动查询当天课表并按配置注册签到任务：

```bash
python3 iclass_checkin.py --query
```

查看已注册的签到任务：

```bash
python3 iclass_checkin.py --show-cron
```

清除所有本工具创建的签到任务：

```bash
python3 iclass_checkin.py --clear-cron
```

手动执行某节课签到：

```bash
python3 iclass_checkin.py --checkin <student_id> <schedule_id>
```

## 日志与状态

| 文件 | 说明 |
|------|------|
| `state/iclass-checkin.log` | 运行日志 (控制台 + 文件双输出) |
| `state/session.json` | WebVPN session 缓存 (6 小时有效) |
| `state/schedule_YYYYMMDD.json` | 当天课表缓存 |

这些运行时文件不会被 Git 提交。

## 签到重试机制

签到失败时，程序会根据错误类型决定是否重试：

- **可重试**: 网络异常、"当前时间不是上课时间" (老师还没发起签到)、服务超时
- **不可重试**: 参数错误、签到已过期、账号异常

可重试的错误会自动重试最多 3 次，每次间隔 30 秒。重试参数在脚本顶部的常量中定义：

```python
SIGN_MAX_RETRIES = 3    # 最大重试次数
SIGN_RETRY_DELAY = 30   # 每次重试间隔 (秒)
```

## 安全说明

`config.json` 包含统一身份认证密码，已加入 `.gitignore`，不要提交到 Git 仓库。
